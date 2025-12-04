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
import urllib.parse
from streamlit_oauth import OAuth2Component
import numpy as np
import hashlib
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.preprocessing import LabelEncoder

# --- 1. CONFIGURAÇÃO GERAL ---
st.set_page_config(
    page_title="AInsight Enterprise", 
    page_icon="👁️", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- 2. MOTOR DE DADOS BLINDADO ---
def clean_money_universal(val):
    if pd.isna(val) or val == '': return 0.0
    s = str(val).strip()
    s_clean = re.sub(r'[^\d.,-]', '', s)
    if not s_clean: return 0.0
    try:
        if ',' in s_clean and '.' in s_clean:
            if s_clean.rfind(',') > s_clean.rfind('.'): s_clean = s_clean.replace('.', '').replace(',', '.')
            else: s_clean = s_clean.replace(',', '')
        elif ',' in s_clean: s_clean = s_clean.replace(',', '.')
        return float(s_clean)
    except: return 0.0

def load_universal_file(uploaded_file):
    try:
        if uploaded_file.name.endswith('.xlsx') or uploaded_file.name.endswith('.xls'):
            return pd.read_excel(uploaded_file)
        encodings = ['utf-8', 'latin-1', 'iso-8859-1', 'cp1252']
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
        row_0 = df.iloc[0]
        if row_0.notna().sum() > (len(df.columns) / 2):
            df.columns = row_0
            df = df.iloc[1:].reset_index(drop=True)
    for col in df.columns:
        if df[col].dtype == 'object':
            try:
                sample = df[col].astype(str).head().tolist()
                if any(re.search(r'\d', x) for x in sample):
                    temp_col = df[col].apply(clean_money_universal)
                    if (temp_col != 0).sum() > 0: df[col] = temp_col
            except: pass
            try: df[col] = pd.to_datetime(df[col]); continue
            except: pass
    return df

# --- 3. BACKEND ROBUSTO ---
HISTORY_FILE = "chat_database.json"

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
            self.user_data = {"chats": {}, "docs": {}, "datasets": {}, "tasks": {}, "notifications": [], "plan": "free", "last_invite_at": None}
        else:
            self.user_data = self.full_db["users"][self.username]
            for k in ["notifications", "datasets", "docs", "tasks"]:
                if k not in self.user_data: self.user_data[k] = [] if k=="notifications" else {}

    def save_db(self):
        if self.username in self.full_db["users"]:
            self.full_db["users"][self.username] = self.user_data
            with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)

    def save_dataset_version(self, name, df):
        if "datasets" not in self.user_data: self.user_data["datasets"] = {}
        did = str(uuid.uuid4())
        djson = df.to_json(orient='split', date_format='iso')
        self.user_data["datasets"][did] = {
            "name": name, "current_version": "v1", "created_at": datetime.now().isoformat(),
            "commits": [{"version": "v1", "msg": "Init", "ts": datetime.now().isoformat(), "data": djson}]
        }
        self.save_db()
        return did

    def get_dataset(self, did):
        if did in self.user_data.get("datasets", {}):
            try: return pd.read_json(StringIO(self.user_data["datasets"][did]["commits"][0]["data"]), orient='split')
            except: return None
        return None
    
    # TAREFAS E DOCS
    def create_task(self, title, desc="", prio="Média"):
        tid = str(uuid.uuid4())
        self.user_data["tasks"][tid] = {"title": title, "desc": desc, "status": "To Do", "prio": prio, "created_at": datetime.now().isoformat()}
        self.save_db()
    
    def move_task(self, tid, status):
        if tid in self.user_data["tasks"]: self.user_data["tasks"][tid]["status"] = status; self.save_db()
    
    def delete_task(self, tid):
        if tid in self.user_data["tasks"]: del self.user_data["tasks"][tid]; self.save_db()

    def create_doc(self, title, content=""):
        did = str(uuid.uuid4())
        self.user_data["docs"][did] = {"title": title, "content": content, "updated_at": datetime.now().isoformat()}
        self.save_db()
    
    def update_doc(self, did, content):
        if did in self.user_data["docs"]: self.user_data["docs"][did]["content"] = content; self.save_db()

    def delete_doc(self, did):
        if did in self.user_data["docs"]: del self.user_data["docs"][did]; self.save_db()

# --- CÉREBRO IA ---
def ask_gemini(df, query, key, persona):
    genai.configure(api_key=key)
    
    # Modelo
    chosen_model = "gemini-pro"
    try:
        for m in genai.list_models():
            if 'flash' in m.name: chosen_model = m.name; break
    except: pass

    # Resumo estatístico dos dados
    summary = df.describe(include='all').to_string()
    
    # PROMPT DE ANALISTA SÉNIOR
    prompt = f"""
    TU ÉS: {persona} (Analista Sénior de Topo).
    
    CONTEXTO DOS DADOS (Para tua análise, NÃO mostres isto ao utilizador):
    {summary}
    
    O QUE O UTILIZADOR PEDIU: "{query}"
    
    ---
    
    AS TUAS INSTRUÇÕES ESTRITAS PARA O RELATÓRIO FINAL:
    
    1. **NÃO MOSTRES TABELAS ESTATÍSTICAS:** O utilizador é um executivo. Não quer ver `count`, `mean`, `std` ou tabelas de `describe()`. Isso é proibido.
    2. **FOCA-TE NOS INSIGHTS:** Em vez de dizeres "A média é 500", diz "A performance média foi positiva, rondando os 500€, o que indica...".
    3. **ESTRUTURA CLARA:**
       - **Título Principal** (Markdown #)
       - **Resumo Executivo:** 2 ou 3 frases com a conclusão principal.
       - **Análise Detalhada:** Explica os pontos altos e baixos.
       - **Recomendações:** O que fazer a seguir?
    4. **VISUALIZAÇÃO OBRIGATÓRIA:** Gera código Python para criar 2 ou 3 gráficos (matplotlib/seaborn) que ilustrem os teus pontos.
    
    REGRAS TÉCNICAS (PYTHON):
    - Responde SÓ com código Python executável em blocos ```python ... ```.
    - O dataframe chama-se 'df'. NÃO uses `pd.read_csv`.
    - Trata valores nulos com `fillna(0)` antes de fazer gráficos.
    - Usa cores profissionais (`sns.set_palette("viridis")`).
    - Usa `plt.figure(figsize=(10,6))` antes de cada gráfico.
    - NÃO USES `print(df.describe())` ou `print(df.head())`.
    """
    
    try:
        model = genai.GenerativeModel(chosen_model)
        res = model.generate_content(prompt)
        match = re.search(r"```python(.*?)```", res.text, re.DOTALL)
        return match.group(1).strip() if match else res.text.replace("```", "").strip()
    except Exception as e: return f"print('Erro na IA: {e}')"

# --- FUNÇÕES AUXILIARES ---
def ext_hash_pass(p): return hashlib.sha256(p.encode()).hexdigest()

def ext_register_user(u, p, e):
    db = HistoryManager()
    if u in db.full_db["users"]: return False, "User existe."
    db.full_db["users"][u] = {
        "password": ext_hash_pass(p), "email": e, "created_at": datetime.now().isoformat(), "plan": "free",
        "notifications": [], "chats": {}, "tasks": {}, "docs": {}, "datasets": {}
    }
    with open(HISTORY_FILE, 'w') as f: json.dump(db.full_db, f, indent=4, default=str)
    return True, "Criado!"

def ext_create_invite(db, owner, data, perm):
    db.load_db()
    rec = db.full_db["users"].get(owner)
    if rec and rec.get("last_invite_at"):
        diff = (datetime.now() - datetime.fromisoformat(rec["last_invite_at"])).days
        if diff < 30: return False, f"Limite mensal. Espera {30-diff} dias."
    
    tk = str(uuid.uuid4())[:8].upper()
    db.full_db["guest_tokens"][tk] = {"created_by": owner, "created_at": datetime.now().isoformat(), "share_data": perm, "invitee": data, "used": False}
    if rec: rec["last_invite_at"] = datetime.now().isoformat()
    with open(HISTORY_FILE, 'w') as f: json.dump(db.full_db, f, indent=4, default=str)
    return True, tk

def ext_consume_invite(tk):
    db = HistoryManager()
    tk = tk.strip().upper()
    if tk in db.full_db["guest_tokens"]:
        d = db.full_db["guest_tokens"][tk]
        if not d["used"]:
            d["used"] = True; d["used_at"] = datetime.now().isoformat()
            with open(HISTORY_FILE, 'w') as f: json.dump(db.full_db, f, indent=4, default=str)
            return True, d
    return False, None

# --- INTERFACES ---

def ui_login():
    # CSS Animado
    st.markdown("""
        <style>
        [data-testid="stAppViewContainer"] {
            background: linear-gradient(-45deg, #0f0c29, #302b63, #24243e);
            background-size: 400% 400%;
            animation: gradient 15s ease infinite;
            color: white;
        }
        @keyframes gradient { 0% {background-position: 0% 50%;} 50% {background-position: 100% 50%;} 100% {background-position: 0% 50%;} }
        </style>
    """, unsafe_allow_html=True)
    
    c1,c2,c3 = st.columns([1,2,1])
    with c2:
        st.title("🔐 AInsight Enterprise")
        t1, t2 = st.tabs(["Entrar", "Criar Conta"])
        with t1:
            u = st.text_input("User"); p = st.text_input("Pass", type="password")
            if st.button("Entrar", use_container_width=True):
                db = HistoryManager(); usr = db.full_db["users"].get(u)
                if (u=="admin" and p=="123") or (usr and usr.get("password") == ext_hash_pass(p)):
                    st.session_state.update({'auth': True, 'user': u, 'guest': False})
                    st.rerun()
                else: st.error("Erro.")
            
            with st.expander("Usar Convite"):
                tk = st.text_input("Código")
                if st.button("Validar"):
                    ok, d = ext_consume_invite(tk)
                    if ok:
                        st.session_state.update({'auth': True, 'user': "Convidado", 'guest': True})
                        if d["share_data"]: st.session_state['guest_viewing_owner'] = d["created_by"]
                        st.rerun()
                    else: st.error("Inválido.")

        with t2:
            nu = st.text_input("Novo User"); ne = st.text_input("Email"); np = st.text_input("Pass", type="password")
            if st.button("Registar", use_container_width=True):
                ok, msg = ext_register_user(nu, np, ne)
                if ok: st.success(msg)
                else: st.error(msg)

def ui_analysis_unified(db, key):
    st.title("📊 Análise IA")
    
    # ZONA DE UPLOAD
    if 'active_df' not in st.session_state:
        with st.container(border=True):
            st.header("📂 Carregar Ficheiro")
            st.caption("Arrasta o teu Excel ou CSV para aqui. A análise começa automaticamente.")
            up = st.file_uploader("Ficheiro", type=['csv', 'xlsx', 'xls'])
            if up:
                with st.spinner("A processar dados..."):
                    try:
                        df = load_universal_file(up)
                        if df is not None:
                            df = smart_clean_dataframe(df)
                            db.save_dataset_version(up.name, df)
                            st.session_state['active_df'] = df
                            st.session_state['active_name'] = up.name
                            st.success("Carregado!"); time.sleep(0.5); st.rerun()
                        else: st.error("Formato desconhecido.")
                    except Exception as e: st.error(str(e))
        
        # Histórico
        if db.user_data.get("datasets"):
            st.divider(); st.subheader("Histórico")
            for did, d in db.user_data["datasets"].items():
                if st.button(f"📄 {d['name']}", key=did):
                    st.session_state['active_df'] = db.get_dataset(did)
                    st.session_state['active_name'] = d['name']
                    st.rerun()
        return

    # ZONA DE ANÁLISE
    df = st.session_state['active_df']
    name = st.session_state['active_name']
    
    c1, c2 = st.columns([3, 1])
    c1.success(f"✅ A Analisar: **{name}** ({len(df)} linhas)")
    if c2.button("❌ Trocar Ficheiro"): del st.session_state['active_df']; st.rerun()
        
    persona = st.selectbox("Persona", ["Data Scientist", "CFO", "CMO", "COO"])
    
    if st.button(f" Relatório Automático ({persona})", type="primary"):
        if not key: st.error("Falta API Key")
        else:
            with st.spinner("A gerar insights..."):
                q = f"Gera um relatório executivo detalhado como {persona}."
                code = ask_gemini(df, q, key, persona)
                txt, fig = execute_code(code, df)
                st.markdown(txt)
                if fig: st.pyplot(fig)
                
                db.create_doc(f"Relatório {name} - {persona}", txt)
                st.toast("Relatório guardado em Docs!")
                with st.expander("Ver Código"): st.code(code)

    if q := st.chat_input("Faz uma pergunta..."):
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
    target = st.selectbox("O que pretende prever?", df.columns)
    
    if st.button("Treinar Modelo"):
        with st.spinner("A aprender padrões..."):
            try:
                dfc = df.copy().dropna()
                for c in dfc.select_dtypes('object'): dfc[c] = LabelEncoder().fit_transform(dfc[c].astype(str))
                X = dfc.drop(columns=[target]); y = dfc[target]
                model = RandomForestRegressor() if (y.dtype!='object' and y.nunique()>10) else RandomForestClassifier()
                model.fit(X, y)
                st.success(f"✅ Modelo treinado para prever '{target}'!")
                st.balloons()
            except Exception as e: st.error(str(e))

def ui_invites(db):
    st.title("📨 Convites")
    with st.form("inv"):
        nm = st.text_input("Nome"); em = st.text_input("Email"); ph = st.text_input("Tel")
        perm = st.checkbox("Partilhar Dados")
        if st.form_submit_button("Enviar"):
            if nm and em:
                ok, res = ext_create_invite(db, db.username, {"email":em}, perm)
                if ok: st.success(f"Código: {res}"); st.toast("Email simulado enviado.")
                else: st.error(res)
            else: st.warning("Preenche dados.")

def ui_tasks(db):
    st.title(" Quadro de Tarefas")
    with st.expander("Nova Tarefa"):
        with st.form("nt"):
            t = st.text_input("Título"); d = st.text_area("Descrição"); p = st.selectbox("Prioridade", ["Alta","Média"])
            if st.form_submit_button("Criar"): db.create_task(t, d, p); st.rerun()
            
    # Kanban Simples
    col1, col2, col3 = st.columns(3)
    tasks = db.user_data.get("tasks", {})
    todo = {k:v for k,v in tasks.items() if v['status'] == 'To Do'}
    doing = {k:v for k,v in tasks.items() if v['status'] == 'Doing'}
    done = {k:v for k,v in tasks.items() if v['status'] == 'Done'}
    
    with col1:
        st.subheader("📌 A Fazer")
        for k,v in todo.items():
            st.info(f"{v['title']}")
            if st.button("➡️", key=f"do_{k}"): db.move_task(k, "Doing"); st.rerun()
            
    with col2:
        st.subheader("⚙️ A Decorrer")
        for k,v in doing.items():
            st.warning(f"{v['title']}")
            if st.button("✅", key=f"ok_{k}"): db.move_task(k, "Done"); st.rerun()
            
    with col3:
        st.subheader("🎉 Feito")
        for k,v in done.items():
            st.success(f"{v['title']}")
            if st.button("🗑️", key=f"del_{k}"): db.delete_task(k); st.rerun()

def ui_docs(db):
    st.title("🧠 Docs & Relatórios")
    col1, col2 = st.columns([1,3])
    with col1:
        if st.button("➕ Novo Doc"): st.session_state['doc_id'] = "new"
        for did, d in db.user_data.get("docs", {}).items():
            if st.button(f"📄 {d['title']}", key=did): st.session_state['doc_id'] = did
            
    with col2:
        did = st.session_state.get('doc_id')
        if did == "new":
            t = st.text_input("Título"); c = st.text_area("Conteúdo", height=400)
            if st.button("Salvar"): db.create_doc(t, c); st.success("Salvo!"); st.rerun()
        elif did and did in db.user_data["docs"]:
            d = db.user_data["docs"][did]
            st.markdown(d['content'])
            if st.button("Apagar Doc"): db.delete_doc(did); st.rerun()

def ui_profile(db):
    st.title("Perfil"); u = db.user_data
    st.info(f"User: {db.username}")
    if st.button("Limpar Notificações"): u["notifications"] = []; db.save_db(); st.rerun()
    for n in u.get("notifications", []): st.write(f"📩 {n['msg']}")

# --- MAIN ---
def main():
    if "auth" not in st.session_state: st.session_state["auth"] = False
    
    if not st.session_state["auth"]:
        ui_login()
    else:
        user = st.session_state["user"]
        guest = st.session_state.get('guest', False)
        
        # Carrega DB (Dono ou Partilhada)
        if guest and st.session_state.get("guest_viewing_owner"):
            db = HistoryManager(st.session_state["guest_viewing_owner"])
        else:
            db = HistoryManager(user)
            
        real_db = HistoryManager(user) if guest else db
        notifs = len(real_db.full_db["users"].get(user, {}).get("notifications", []))
        lbl_n = f"🔔 ({notifs})" if notifs > 0 else "🔔"
        
        with st.sidebar:
            st.header("AInsight Pro"); st.caption(f"User: {user}")
            opts = [" Análise ", " ML Studio", " Tarefas", " Docs", lbl_n]
            if not guest: opts.insert(2, " Convites")
            opts.append("👤 Perfil")
            
            # Navegação Forçada
            idx = 0
            if 'force_page' in st.session_state and st.session_state['force_page'] in opts:
                idx = opts.index(st.session_state['force_page']); del st.session_state['force_page']
            
            page = st.radio("Menu", opts, index=idx)
            if st.button("Sair"): st.session_state.clear(); st.rerun()
            
        key = st.secrets.get("GEMINI_API_KEY", "")
        
        if "Análise" in page: ui_analysis_unified(db, key)
        elif "ML Studio" in page: ui_ml(db)
        elif "Convites" in page: ui_invites(db)
        elif "Tarefas" in page: ui_tasks(db)
        elif "Docs" in page: ui_docs(db)
        elif "Perfil" in page or "Notificações" in page: ui_profile(real_db)

if __name__ == "__main__":
    main()