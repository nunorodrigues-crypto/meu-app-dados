import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import google.generativeai as genai
import sys
from io import StringIO, BytesIO
import re
import json
import os
import uuid
from datetime import datetime, timedelta
import requests
import qrcode
import time
import urllib.parse
from streamlit_oauth import OAuth2Component
import numpy as np
import hashlib
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.preprocessing import LabelEncoder

# --- 1. CONFIGURAÇÃO GERAL ---
st.set_page_config(page_title="AInsight Enterprise", page_icon="👁️", layout="wide")
HISTORY_FILE = "chat_database.json"

# --- 2. MOTOR DE DADOS ---
def load_universal_file(uploaded_file):
    try:
        if uploaded_file.name.endswith('.xlsx') or uploaded_file.name.endswith('.xls'):
            return pd.read_excel(uploaded_file)
        encodings = ['utf-8', 'latin-1', 'cp1252']
        for enc in encodings:
            try:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, sep=None, engine='python', encoding=enc)
            except: continue
        return None
    except: return None

def smart_clean_dataframe(df):
    df.dropna(how='all', inplace=True)
    df.dropna(how='all', axis=1, inplace=True)
    if len(df) > 1 and any("Unnamed" in str(c) for c in df.columns):
        if df.iloc[0].notna().sum() > (len(df.columns) / 2):
            df.columns = df.iloc[0]
            df = df.iloc[1:].reset_index(drop=True)
    return df

# --- 3. BACKEND ---
class HistoryManager:
    def __init__(self, username="system"):
        self.username = username
        self.load_db()

    def load_db(self):
        if not os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'w') as f: json.dump({"users": {}, "guest_tokens": {}}, f)
        try:
            with open(HISTORY_FILE, 'r') as f: self.full_db = json.load(f)
        except: self.full_db = {"users": {}, "guest_tokens": {}}
        
        if "users" not in self.full_db: self.full_db["users"] = {}
        if self.username not in self.full_db["users"]:
            self.user_data = {"chats": {}, "docs": {}, "datasets": {}, "tasks": {}, "notifications": [], "plan": "free"}
        else:
            self.user_data = self.full_db["users"][self.username]
            for k in ["notifications", "datasets", "docs", "tasks"]:
                if k not in self.user_data: self.user_data[k] = [] if k=="notifications" else {}

    def save_db(self):
        if self.username in self.full_db["users"]:
            self.full_db["users"][self.username] = self.user_data
            with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
    def save_dataset(self, name, df):
        did = str(uuid.uuid4())
        djson = df.to_json(orient='split', date_format='iso')
        self.user_data["datasets"][did] = {
            "name": name, "created_at": datetime.now().isoformat(),
            "commits": [{"version": "v1", "data": djson}]
        }
        self.save_db()
    
    def create_doc(self, title, content):
        """Guarda um documento simples nos docs do utilizador."""
        did = str(uuid.uuid4())
        self.user_data["docs"][did] = {
            "title": title,
            "content": content,
            "created_at": datetime.now().isoformat()
        }
        self.save_db()
        
    def delete_doc(self, did):
        if did in self.user_data["docs"]: del self.user_data["docs"][did]; self.save_db()

# --- GESTÃO DE TAREFAS (BACKEND) ---
    def create_task(self, t, desc="", prio="Média", assignee=""):
        tid = str(uuid.uuid4())
        self.user_data["tasks"][tid] = {
            "title": t, 
            "description": desc, 
            "priority": prio, 
            "status": "To Do",
            "assignee": assignee, 
            "history": [], 
            "created_at": datetime.now().isoformat()
        }
        self.save_db()

    def add_task_comment(self, tid, msg):
        if tid in self.user_data["tasks"]:
            if "history" not in self.user_data["tasks"][tid]: 
                self.user_data["tasks"][tid]["history"] = []
            
            self.user_data["tasks"][tid]["history"].append({
                "user": self.username, 
                "msg": msg, 
                "ts": datetime.now().strftime("%d/%m %H:%M")
            })
            self.save_db()
    
    def move_task(self, tid, status):
        if tid in self.user_data["tasks"]:
            self.user_data["tasks"][tid]["status"] = status
            self.add_task_comment(tid, f"Mudou para: {status}")
            self.save_db()
            
    def delete_task(self, tid):
        if tid in self.user_data["tasks"]: 
            del self.user_data["tasks"][tid]
            self.save_db()

# --- 4. CÉREBRO IA (MODO PRECISÃO) ---
def ask_gemini(df, query, key, persona):
    genai.configure(api_key=key)
    chosen_model = "gemini-pro"
    try:
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if any('flash' in m for m in models): chosen_model = next(m for m in models if 'flash' in m)
    except: pass

    # Contexto reduzido
    summary = df.describe(include='all').to_string()
    
    prompt = f"""
    ATUA COMO: {persona}.
    DADOS: {summary}
    PEDIDO: "{query}"
    
    REGRAS TÉCNICAS ESTRITAS (PARA EVITAR ERROS DE CÓDIGO):
    1. O teu objetivo é APENAS gerar código Python.
    2. **NÃO USES A BIBLIOTECA `locale`**. Para formatar dinheiro, usa f-strings simples (ex: `f"{{valor:,.2f}} €"`).
    3. NÃO uses `datetime_is_numeric`.
    4. Usa `print(f"## Título")` para texto.
    5. Usa `plt.figure(figsize=(10,6))` e `sns.barplot` ou `sns.lineplot`.
    6. O dataframe chama-se 'df'.
    7. Antes de qualquer gráfico, faz `plt.clf()` para limpar a memória.
    """
    try:
        model = genai.GenerativeModel(chosen_model)
        # Temperature 0 para máxima precisão técnica
        res = model.generate_content(prompt, generation_config={"temperature": 0})
        match = re.search(r"```python(.*?)```", res.text, re.DOTALL)
        return match.group(1).strip() if match else res.text.replace("```", "").strip()
    except Exception as e: return f"print('Erro IA: {e}')"

def execute_code(code, df):
    try:
        code = code.replace(", datetime_is_numeric=True", "")
        old = sys.stdout; sys.stdout = StringIO()
        local_vars = {'df': df, 'pd': pd, 'plt': plt, 'sns': sns, 'np': np}
        exec(code, {}, local_vars)
        out = sys.stdout.getvalue(); sys.stdout = old
        fig = plt.gcf()
        if not plt.gca().has_data(): fig = None
        else: plt.clf()
        return out, fig
    except Exception as e:
        sys.stdout = sys.__stdout__
        return f"Erro de Sintaxe no Código Gerado:\n{e}\n\nCódigo:\n{code}", None

# --- 5. UI ---
def ui_login():
    st.markdown("## 🔐 AInsight Enterprise")
    t1, t2 = st.tabs(["Entrar", "Registar"])
    with t1:
        u = st.text_input("User"); p = st.text_input("Pass", type="password")
        if st.button("Entrar"):
            db = HistoryManager(); usr = db.full_db["users"].get(u)
            if (u=="admin" and p=="123") or (usr and usr.get("password") == hashlib.sha256(p.encode()).hexdigest()):
                st.session_state.update({'auth': True, 'user': u})
                st.rerun()
            else: st.error("Erro.")
    with t2:
        nu = st.text_input("Novo User"); np = st.text_input("Nova Pass", type="password")
        if st.button("Registar"):
            db = HistoryManager()
            if nu not in db.full_db["users"]:
                db.full_db["users"][nu] = {"password": hashlib.sha256(np.encode()).hexdigest(), "plan":"free"}
                with open(HISTORY_FILE, 'w') as f: json.dump(db.full_db, f, indent=4, default=str)
                st.success("Criado!"); st.rerun()

def ui_analysis(db, key):
    st.title("📊 Análise IA")
    
    if 'active_df' not in st.session_state:
        with st.container(border=True):
            st.header("📂 Carregar Ficheiro")
            up = st.file_uploader("Ficheiro Excel/CSV", type=['csv', 'xlsx'])
            if up:
                with st.spinner("A carregar..."):
                    df = load_universal_file(up)
                    if df is not None:
                        df = smart_clean_dataframe(df)
                        db.save_dataset(up.name, df)
                        st.session_state['active_df'] = df
                        st.session_state['active_name'] = up.name
                        st.rerun()
                    else: st.error("Erro ao ler.")
        return

    df = st.session_state['active_df']
    name = st.session_state['active_name']
    
    c1, c2 = st.columns([3, 1])
    c1.success(f"✅ Analisando: **{name}** ({len(df)} linhas)")
    if c2.button("❌ Trocar"): del st.session_state['active_df']; st.rerun()
    
    persona = st.selectbox("Persona", ["Data Scientist", "CFO", "CMO", "COO"])
    
    if st.button(f"🚀 Relatório Automático ({persona})", type="primary"):
        if not key: st.error("Falta API Key")
        else:
            with st.spinner("A analisar (Modo Precisão)..."):
                q = f"Gera relatório completo de {persona} com gráficos e insights."
                code = ask_gemini(df, q, key, persona)
                txt, fig = execute_code(code, df)
                
                st.markdown("---")
                if txt: st.markdown(txt)
                if fig: st.pyplot(fig)
                
                db.create_doc(f"Relatório {persona}", txt)
                st.toast("Guardado em Docs!")

    if q := st.chat_input("Pergunta..."):
        with st.chat_message("user"): st.write(q)
        with st.chat_message("assistant"):
            code = ask_gemini(df, q, key, persona)
            txt, fig = execute_code(code, df)
            st.write(txt)
            if fig: st.pyplot(fig)

def ui_ml(db):
    st.title("🤖 ML Studio")
    if 'active_df' not in st.session_state: st.warning("Carrega ficheiro na Análise IA primeiro."); return
    df = st.session_state['active_df']
def ui_tasks(db):
    st.title("🔨 Quadro de Tarefas & Equipa")
    
    # 1. CRIAR NOVA TAREFA (Agora com campo para atribuir)
    with st.expander("➕ Nova Tarefa", expanded=False):
        with st.form("add_task"):
            c1, c2 = st.columns([2, 1])
            t = c1.text_input("Título da Tarefa")
            assignee = c2.text_input("Atribuir a (Nome/Email)")
            d = st.text_area("Descrição")
            p = st.selectbox("Prioridade", ["Alta", "Média", "Baixa"])
            
            if st.form_submit_button("Criar Tarefa"):
                if t:
                    db.create_task(t, d, p, assignee)
                    st.success("Tarefa criada!")
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.warning("O título é obrigatório.")
    
    st.divider()
    
    # 2. LISTAGEM (CARDS COM HISTÓRICO)
    tasks = db.user_data.get("tasks", {})
    if not tasks:
        st.info("Não há tarefas pendentes.")
        return

    # Ordena: Alta prioridade primeiro
    sorted_tasks = sorted(tasks.items(), key=lambda x: (x[1].get('priority') != 'Alta', x[1].get('created_at')), reverse=False)

    for tid, task in sorted_tasks:
        # Corzinha visual para prioridade
        prio_icon = "🔥" if task.get('priority') == "Alta" else "📌"
        
        with st.container(border=True):
            col_info, col_actions = st.columns([5, 1])
            
            # --- LADO ESQUERDO: INFORMAÇÃO ---
            with col_info:
                st.markdown(f"### {prio_icon} {task.get('title')}")
                if task.get('description'): st.caption(task['description'])
                
                # Badges de quem é e estado
                quem = task.get('assignee') if task.get('assignee') else "Sem dono"
                estado = task.get('status', 'To Do')
                st.markdown(f"👤 **{quem}** | Estado: `{estado}`")

                # --- CHAT / HISTÓRICO DENTRO DA TAREFA ---
                history = task.get('history', [])
                with st.expander(f"💬 Comentários ({len(history)})"):
                    # Mostra mensagens antigas
                    for h in history:
                        st.text(f"[{h['ts']}] {h['user']}: {h['msg']}")
                    
                    # Caixa para escrever nova mensagem
                    c_txt, c_btn = st.columns([4, 1])
                    new_msg = c_txt.text_input("Escrever...", key=f"txt_{tid}", label_visibility="collapsed")
                    if c_btn.button("Enviar", key=f"btn_{tid}"):
                        if new_msg:
                            db.add_task_comment(tid, new_msg)
                            st.rerun()

            # --- LADO DIREITO: BOTÕES DE AÇÃO ---
            with col_actions:
                st.write("") # Espaçamento
                if estado == "To Do":
                    if st.button("▶️", key=f"start_{tid}", help="Iniciar"): db.move_task(tid, "Doing"); st.rerun()
                elif estado == "Doing":
                    if st.button("✅", key=f"done_{tid}", help="Concluir"): db.move_task(tid, "Feito"); st.rerun()
                
                if st.button("🗑️", key=f"del_{tid}", help="Apagar"):
                    db.delete_task(tid)
                    st.rerun()
    # fim da listagem de tarefas
    return

def ui_others(db, page):
    if page == "🧠 Docs":
        st.title("Docs")
        for k, v in db.user_data.get("docs", {}).items():
            with st.expander(v.get('title', 'Documento')):
                st.markdown(v.get('content', ''))
                if st.button("Apagar", key=f"del_doc_{k}"):
                    db.delete_doc(k)
                    st.rerun()
    elif page == "📨 Convites":
        st.title("Convites")
        st.info("Funcionalidade de convites ativa.")
    elif page == "👤 Perfil":
        st.title("Perfil")
        st.info(f"User: {db.username}")
        for k,v in db.user_data.get("docs", {}).items():
            with st.expander(v['title']):
                st.markdown(v['content'])
                if st.button("Apagar", key=k): db.delete_doc(k); st.rerun()
                
    elif page == "📨 Convites":
        st.title("Convites")
        st.info("Funcionalidade de convites ativa.")
        
    elif page == "👤 Perfil":
        st.title("Perfil"); st.info(f"User: {db.username}")

def main():
    if "auth" not in st.session_state: st.session_state["auth"] = False
    
    if not st.session_state["auth"]:
        ui_login()
    else:
        user = st.session_state["user"]
        db = HistoryManager(user)
        key = st.secrets.get("GEMINI_API_KEY", "")
        
        with st.sidebar:
            st.header("AInsight Pro"); st.caption(f"User: {user}")
            page = st.radio("Menu", ["📊 Análise IA", "🤖 ML Studio", "🔨 Tarefas", "🧠 Docs", "📨 Convites", "👤 Perfil"])
            if st.button("Sair"): st.session_state.clear(); st.rerun()
            
        if page == "📊 Análise IA": ui_analysis(db, key)
        elif page == "🤖 ML Studio": ui_ml(db)
        else: ui_others(db, page)

if __name__ == "__main__":
    main()