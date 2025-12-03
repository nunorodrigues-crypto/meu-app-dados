import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import google.generativeai as genai
import sys
from io import StringIO, BytesIO
import re
from functools import reduce
from fpdf import FPDF
import time
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
    page_title="AInsight Pro", 
    page_icon="👁️", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- 2. MOTOR DE DADOS UNIVERSAL (A "BLINDAGEM") ---

def clean_money_universal(val):
    """
    O Exterminador de Formatos de Moeda.
    Converte: "1.200,50 €", "R$ 1,200.50", "1 000$" -> float puro.
    """
    if pd.isna(val) or val == '': return 0.0
    s = str(val).strip()
    
    # 1. Manter apenas números, pontos, vírgulas e sinal negativo
    s_clean = re.sub(r'[^\d.,-]', '', s)
    if not s_clean: return 0.0
    
    try:
        # 2. Detetar formato (Europeu vs Americano)
        if ',' in s_clean and '.' in s_clean:
            # Se a vírgula vier depois do ponto (1.200,50) -> Europeu
            if s_clean.rfind(',') > s_clean.rfind('.'):
                s_clean = s_clean.replace('.', '').replace(',', '.')
            else: # Americano (1,200.50)
                s_clean = s_clean.replace(',', '')
        elif ',' in s_clean:
            # Só vírgula (1200,50) -> Assume decimal
            s_clean = s_clean.replace(',', '.')
            
        return float(s_clean)
    except:
        return 0.0

def load_universal_file(uploaded_file):
    """
    Lê qualquer ficheiro (CSV/Excel) independentemente da origem,
    língua ou separador (; ou ,).
    """
    try:
        # A. Se for Excel
        if uploaded_file.name.endswith('.xlsx') or uploaded_file.name.endswith('.xls'):
            return pd.read_excel(uploaded_file)
        
        # B. Se for CSV (O mais difícil)
        # Tenta várias codificações (UTF-8, Latin-1 para Windows antigo, etc.)
        encodings = ['utf-8', 'latin-1', 'iso-8859-1', 'cp1252']
        
        for enc in encodings:
            try:
                uploaded_file.seek(0)
                # 'sep=None' obriga o Python a "cheirar" o separador (auto-detect)
                return pd.read_csv(uploaded_file, sep=None, engine='python', encoding=enc)
            except:
                continue # Tenta o próximo
                
        return None # Falhou tudo
    except Exception as e:
        return None

def smart_clean_dataframe(df):
    """
    Limpa o Dataframe final para a IA não se baralhar.
    """
    # 1. Estrutura: Apaga linhas/colunas totalmente vazias
    df.dropna(how='all', inplace=True)
    df.dropna(how='all', axis=1, inplace=True)
    
   # Se detetar colunas chamadas "Unnamed", assume que o cabeçalho está na linha errada
    if len(df) > 1 and any("Unnamed" in str(c) for c in df.columns):
        row_0 = df.iloc[0]
        # Se a primeira linha tiver dados reais, promove-a a cabeçalho
        if row_0.notna().sum() > (len(df.columns) / 2):
            df.columns = row_0
            df = df.iloc[1:].reset_index(drop=True)

    # 3. Conversão de Tipos (Inteligente)
    for col in df.columns:
        if df[col].dtype == 'object':
            # A. Tenta converter Dinheiro/Números
            try:
                # Verifica numa amostra se parece dinheiro
                sample = df[col].astype(str).head().tolist()
                if any(re.search(r'\d', x) for x in sample):
                    # Tenta converter
                    temp_col = df[col].apply(clean_money_universal)
                    # Se a maioria não for zero, aceita a conversão
                    if (temp_col != 0).sum() > 0:
                        df[col] = temp_col
                        continue
            except: pass

            # B. Tenta converter Datas
            try: df[col] = pd.to_datetime(df[col]); continue
            except: pass
            
    return df

# --- 2. GESTOR DE BASE DE DADOS (JSON) ---
HISTORY_FILE = "chat_database.json"

class HistoryManager:
    def __init__(self, username="system"):
        self.username = username
        self.load_db()

def load_db(self):
        if not os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'w') as f: json.dump({"users": {}, "guest_tokens": {}, "workspaces": {}}, f)
        try:
            with open(HISTORY_FILE, 'r') as f: self.full_db = json.load(f)
        except: self.full_db = {"users": {}, "guest_tokens": {}, "workspaces": {}}
        
        # Integridade
        for k in ["users", "guest_tokens", "workspaces"]: 
            if k not in self.full_db: self.full_db[k] = {}

        if self.username not in self.full_db["users"]:
            self.user_data = {
                "chats": {}, "tasks": {}, "docs": {}, "datasets": {}, 
                "plan": "free", "notifications": [], "last_invite_at": None
            }
        else:
            self.user_data = self.full_db["users"][self.username]
            # Garante chaves
            for k in ["notifications", "datasets", "docs", "tasks"]:
                if k not in self.user_data: self.user_data[k] = [] if k == "notifications" else {}
def save_db(self):
    """
    Saves the current user's data to the database file.
    Updates the user's record in the full database and writes the changes to HISTORY_FILE.
    """
    if self.username in self.full_db["users"]:
        self.full_db["users"][self.username] = self.user_data
        with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)

    # --- MÉTODOS DE DADOS ---
def create_task(self, title, description="", priority="Média", assignee=None):
        task_id = str(uuid.uuid4())
        if "tasks" not in self.user_data: self.user_data["tasks"] = {}
        self.user_data["tasks"][task_id] = {
            "title": title, "description": description, "status": "To Do",
            "priority": priority, "assignee": assignee, "created_at": datetime.now().isoformat()
        }
        self.save_db()
        return task_id

def move_task(self, task_id, new_status):
        if task_id in self.user_data.get("tasks", {}):
            self.user_data["tasks"][task_id]["status"] = new_status
            self.save_db()

def delete_task(self, task_id):
        if task_id in self.user_data.get("tasks", {}):
            del self.user_data["tasks"][task_id]
            self.save_db()

def create_doc(self, title, content="# Novo Doc"):
        doc_id = str(uuid.uuid4())
        if "docs" not in self.user_data: self.user_data["docs"] = {}
        self.user_data["docs"][doc_id] = {"title": title, "content": content, "updated_at": datetime.now().isoformat()}
        self.save_db()
        return doc_id

def update_doc(self, doc_id, content):
        if doc_id in self.user_data.get("docs", {}):
            self.user_data["docs"][doc_id]["content"] = content
            self.user_data["docs"][doc_id]["updated_at"] = datetime.now().isoformat()
            self.save_db()

def delete_doc(self, doc_id):
        if doc_id in self.user_data.get("docs", {}):
            del self.user_data["docs"][doc_id]
            self.save_db()

def save_dataset_version(self, name, df):
        if "datasets" not in self.user_data: self.user_data["datasets"] = {}
        # Garante nome único simples
        did = str(uuid.uuid4())
        djson = df.to_json(orient='split', date_format='iso')
        
        self.user_data["datasets"][did] = {
            "name": name,
            "current_version": "v1",
            "commits": [{"version": "v1", "msg": "Init", "ts": datetime.now().isoformat(), "data": djson}]
        }
        self.save_db()
        return did

# --- 3. FUNÇÕES UTILITÁRIAS ---
def ext_hash_pass(password):
    return hashlib.sha256(password.encode()).hexdigest()

def ext_register_user(db, username, password, email):
    if username in db.full_db["users"]: return False, "Utilizador já existe."
    db.full_db["users"][username] = {
        "password": ext_hash_pass(password), "email": email, "created_at": datetime.now().isoformat(),
        "plan": "free", "last_invite_at": None, "notifications": [],
        "chats": {}, "tasks": {}, "docs": {}, "datasets": {}, "workspaces": []
    }
    with open(HISTORY_FILE, 'w') as f: json.dump(db.full_db, f, indent=4, default=str)
    return True, "Conta criada com sucesso!"

def ext_send_notification(db, target_username, message):
    db.load_db()
    if target_username in db.full_db["users"]:
        user = db.full_db["users"][target_username]
        if "notifications" not in user: user["notifications"] = []
        user["notifications"].insert(0, {"msg": message, "read": False, "timestamp": datetime.now().isoformat()})
        with open(HISTORY_FILE, 'w') as f: json.dump(db.full_db, f, indent=4, default=str)

def ext_save_dataset_version(db, name, description, df, parent_id=None):
    if "datasets" not in db.user_data: db.user_data["datasets"] = {}
    data_json = df.to_json(orient='split', date_format='iso')
    timestamp = datetime.now().isoformat()
    
    if parent_id and parent_id in db.user_data["datasets"]:
        ds = db.user_data["datasets"][parent_id]
        new_v = f"v{len(ds['commits']) + 1}"
        ds["commits"].insert(0, {"version": new_v, "message": description, "timestamp": timestamp, "author": db.username, "data_snapshot": data_json})
        ds["current_version"] = new_v
        ext_send_notification(db, db.username, f"Nova versão {new_v} em '{name}'")
        ds_id = parent_id
    else:
        ds_id = str(uuid.uuid4())
        db.user_data["datasets"][ds_id] = {
            "name": name, "description": description, "created_at": timestamp, "owner": db.username, "current_version": "v1",
            "commits": [{"version": "v1", "message": "Importação Inicial", "timestamp": timestamp, "author": db.username, "data_snapshot": data_json}]
        }
        ext_send_notification(db, db.username, f"Dataset '{name}' criado.")
    
    db.save_db()
    return ds_id if not parent_id else parent_id

def ext_get_dataset(db, ds_id):
    if "datasets" in db.user_data and ds_id in db.user_data["datasets"]:
        try:
            json_data = db.user_data["datasets"][ds_id]["commits"][0]["data_snapshot"]
            return pd.read_json(StringIO(json_data), orient='split')
        except: return None
    return None

def ext_create_invite_advanced(db, owner_username, invitee_data, share_permission):
    db.load_db()
    user_record = db.full_db["users"].get(owner_username)
    if user_record and user_record.get("last_invite_at"):
        last_date = datetime.fromisoformat(user_record["last_invite_at"])
        diff = (datetime.now() - last_date).days
        if diff < 30: return False, f"Limite mensal atingido. Espera {30-diff} dias."
    
    token = str(uuid.uuid4())[:8].upper()
    if "guest_tokens" not in db.full_db: db.full_db["guest_tokens"] = {}
    db.full_db["guest_tokens"][token] = {
        "created_by": owner_username, "created_at": datetime.now().isoformat(),
        "share_data": share_permission, "invitee": invitee_data, "used": False
    }
    if user_record: user_record["last_invite_at"] = datetime.now().isoformat()
    
    with open(HISTORY_FILE, 'w') as f: json.dump(db.full_db, f, indent=4, default=str)
    return True, token

def ext_consume_invite(token):
    db = HistoryManager()
    token = token.strip().upper()
    if "guest_tokens" in db.full_db and token in db.full_db["guest_tokens"]:
        t_data = db.full_db["guest_tokens"][token]
        if not t_data["used"]:
            t_data["used"] = True; t_data["used_at"] = datetime.now().isoformat()
            with open(HISTORY_FILE, 'w') as f: json.dump(db.full_db, f, indent=4, default=str)
            return True, t_data
    return False, None

def smart_clean_dataframe(df):
    df.dropna(how='all', inplace=True)
    for col in df.columns:
        if df[col].dtype == 'object':
            try: df[col] = pd.to_datetime(df[col])
            except: pass
    return df

# --- 4. CÉREBRO IA (CORRIGIDO PARA ARGUMENTOS FLEXÍVEIS E MODELO AUTOMÁTICO) ---

def ask_gemini(df, query, api_key, persona):
    genai.configure(api_key=api_key)
    # Modelo dinâmico para evitar erro 404
    chosen_model = "gemini-pro"
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'flash' in m.name: chosen_model = m.name; break
                elif '1.5-pro' in m.name: chosen_model = m.name
    except: pass

    # Resumo para a IA não ler tudo (poupa tokens e evita erros)
    summary = df.describe(include='all').to_string()
    
    prompt = f"""
    Atue como {persona}.
    RESUMO DOS DADOS:
    {summary}
    
    PERGUNTA: "{query}"
    
    REGRAS:
    1. Responda SÓ com código Python em blocos ```python```.
    2. O dataframe chama-se 'df'. NÃO uses pd.read_csv.
    3. Importa pandas, matplotlib.pyplot, seaborn.
    4. Gráficos: plt.figure(figsize=(10,6)).
    5. NÃO uses 'datetime_is_numeric'.
    """
    try:
        model = genai.GenerativeModel(chosen_model)
        res = model.generate_content(prompt)
        match = re.search(r"```python(.*?)```", res.text, re.DOTALL)
        return match.group(1).strip() if match else res.text.replace("```", "").strip()
    except Exception as e: return f"print('Erro IA: {e}')"

def execute_code(code, df):
    try:
        # Limpeza preventiva de erros comuns
        code = code.replace(", datetime_is_numeric=True", "")
        
        old = sys.stdout; sys.stdout = StringIO()
        exec(code, {}, {'df': df, 'pd': pd, 'plt': plt, 'sns': sns, 'np': np})
        out = sys.stdout.getvalue(); sys.stdout = old
        fig = plt.gcf()
        if not plt.gca().has_data(): fig = None
        else: plt.clf()
        return out, fig
    except Exception as e:
        sys.stdout = sys.__stdout__
        return f"Erro Código: {e}", None

def generate_role_insights(df, persona, api_key, *args, **kwargs):
    # Resolve o erro "takes 3 but 5 given" usando *args
    queries = {
        "CFO (Financeiro)": "Resumo financeiro: Receitas, Custos e Margens (com gráficos).",
        "CMO (Marketing)": "Top produtos vendidos e performance de canais.",
        "COO (Operacional)": "Volume de pedidos, eficiência e prazos.",
        "Data Scientist": "Análise exploratória: describe, correlações e valores nulos."
    }
    query = queries.get(persona, "Resumo Geral")
    # Agora aceita argumentos extra sem crashar
    code = ask_gemini(df, query, api_key, persona)
    return query, code

def create_pdf(chat_data):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt=f"Relatorio: {chat_data.get('title', 'Analise')}", ln=1, align='C')
    pdf.ln(10)
    if chat_data.get("notes"):
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, txt="NOTAS", ln=1)
        pdf.set_font("Arial", size=10)
        clean_notes = chat_data.get("notes", "").encode('latin-1', 'replace').decode('latin-1')
        pdf.multi_cell(0, 10, txt=clean_notes)
        pdf.ln(10)
    for msg in chat_data.get("messages", []):
        role = "IA" if msg['role'] == "assistant" else "USER"
        pdf.set_font("Arial", 'B', 10); pdf.cell(0, 10, txt=f"[{role}]", ln=1)
        pdf.set_font("Arial", size=10); pdf.multi_cell(0, 10, txt=msg["content"].encode('latin-1','replace').decode('latin-1'))
        pdf.ln(5)
    return pdf.output(dest='S').encode('latin-1')

# --- 5. INTERFACES (FRONTEND) ---

def render_login_page():
    st.markdown("<h1 style='text-align: center;'>🔐 AInsight Pro</h1>", unsafe_allow_html=True)
    t1, t2 = st.tabs(["Entrar", "Criar Conta"])
    
    with t1:
        with st.form("login_form"):
            u = st.text_input("Utilizador"); p = st.text_input("Password", type="password")
            if st.form_submit_button("Entrar"):
                db = HistoryManager()
                user = db.full_db["users"].get(u)
                valid = False
                if user:
                    pwd = user.get("password")
                    if not pwd or pwd == ext_hash_pass(p): valid = True
                
                if (u == "admin" and p == "123") or valid:
                    st.session_state.update({'authenticated': True, 'username': u, 'is_guest': False})
                    st.rerun()
                else: st.error("Dados inválidos.")
        
        if "GOOGLE_CLIENT_ID" in st.secrets:
            try:
                oauth2 = OAuth2Component(st.secrets["GOOGLE_CLIENT_ID"], st.secrets["GOOGLE_CLIENT_SECRET"], "[https://accounts.google.com/o/oauth2/v2/auth](https://accounts.google.com/o/oauth2/v2/auth)", "[https://oauth2.googleapis.com/token](https://oauth2.googleapis.com/token)", "[https://www.googleapis.com/oauth2/v1/tokeninfo](https://www.googleapis.com/oauth2/v1/tokeninfo)", "[https://www.googleapis.com/oauth2/v1/userinfo](https://www.googleapis.com/oauth2/v1/userinfo)")
                res = oauth2.authorize_button("Entrar com Google", "[https://www.google.com.tw/favicon.ico](https://www.google.com.tw/favicon.ico)", st.secrets["GOOGLE_REDIRECT_URI"], "email profile")
                if res and "token" in res:
                    st.session_state.update({'authenticated': True, 'username': res.get("email"), 'is_guest': False})
                    st.rerun()
            except: pass

        with st.expander("🎫 Usar Código de Convite"):
            tk = st.text_input("Código")
            if st.button("Validar"):
                ok, t_data = ext_consume_invite(tk)
                if ok:
                    st.session_state.update({'authenticated': True, 'username': "Convidado", 'is_guest': True})
                    if t_data.get("share_data"):
                        st.session_state['guest_viewing_owner'] = t_data["created_by"]
                        st.success(f"A entrar como convidado de {t_data['created_by']}...")
                    else: st.success("A iniciar sessão limpa...")
                    time.sleep(1); st.rerun()
                else: st.error("Código inválido.")

    with t2:
        with st.form("reg_form"):
            nu = st.text_input("User"); nm = st.text_input("Email"); np = st.text_input("Password", type="password")
            if st.form_submit_button("Registar"):
                ok, msg = ext_register_user(HistoryManager(), nu, np, nm)
                if ok: st.success(msg)
                else: st.error(msg)

def render_profile_page(db):
    st.title("👤 Perfil")
    db.load_db()
    u_data = db.full_db["users"].get(db.username, {})
    
    c1, c2 = st.columns(2)
    with c1:
        st.info(f"**User:** {db.username}")
        st.info(f"**Email:** {u_data.get('email','N/A')}")
        last = u_data.get('last_invite_at', 'Nunca')[:10] if u_data.get('last_invite_at') else 'Nunca'
        st.info(f"**Último Convite:** {last}")
    
    with c2.form("pass_change"):
        p1 = st.text_input("Nova Password", type="password")
        if st.form_submit_button("Alterar"):
            u_data["password"] = ext_hash_pass(p1)
            with open(HISTORY_FILE, 'w') as f: json.dump(db.full_db, f, default=str)
            st.success("Alterada!")

    st.divider()
    st.subheader("🔔 Notificações")
    if st.button("Limpar"):
        u_data["notifications"] = []
        with open(HISTORY_FILE, 'w') as f: json.dump(db.full_db, f, default=str)
        st.rerun()
        
    for n in u_data.get("notifications", []):
        icon = "📩" if not n['read'] else "✅"
        st.write(f"{icon} {n['msg']}")
        n['read'] = True
    with open(HISTORY_FILE, 'w') as f: json.dump(db.full_db, f, default=str)

def render_invites_page(db):
    st.title("📨 Convites")
    user_rec = db.full_db["users"].get(db.username, {})
    last_inv = user_rec.get("last_invite_at")
    can_invite = True
    days = 0
    
    if last_inv:
        diff = (datetime.now() - datetime.fromisoformat(last_inv)).days
        if diff < 30: can_invite = False; days = 30 - diff
    
    if not can_invite:
        st.error(f"🚫 Limite atingido. Espera {days} dias.")
    else:
        with st.form("inv_form"):
            name = st.text_input("Nome"); email = st.text_input("Email"); phone = st.text_input("Telefone")
            perm = st.checkbox("Partilhar Dados (Leitura)")
            if st.form_submit_button("Enviar"):
                if name and email:
                    ok, res = ext_create_invite_advanced(db, db.username, {"name":name, "email":email, "phone":phone}, perm)
                    if ok:
                        st.session_state['last_token'] = res
                        st.success("Convite Enviado!")
                        st.toast(f"Email simulado para {email}")
                        st.rerun()
                    else: st.error(res)
                else: st.warning("Preenche tudo.")
    
    if 'last_token' in st.session_state:
        st.success(f"Código: {st.session_state['last_token']}")

def render_data_hub(db):
    st.title("🧬 Data Hub")
    is_guest = st.session_state.get("is_guest", False)
    
    # 1. Upload (Apenas se não for convidado)
    if not is_guest:
        with st.expander("➕ Novo Dataset"):
            up = st.file_uploader("Ficheiro")
            nm = st.text_input("Nome")
            if st.button("Criar") and up:
                try:
                    df = pd.read_csv(up) if up.name.endswith('.csv') else pd.read_excel(up)
                    df = smart_clean_dataframe(df)
                    ext_save_dataset_version(db, nm, "Upload", df)
                    st.success("Criado!"); time.sleep(0.5); st.rerun()
                except Exception as e: st.error(f"Erro: {e}")
    
    # 2. Listagem
    if "datasets" not in db.user_data or not db.user_data["datasets"]: 
        st.info("Vazio."); return
    
    for did, data in db.user_data["datasets"].items():
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.markdown(f"**{data['name']}** ({data['current_version']})")
            
            # --- CORREÇÃO AQUI ---
            if c2.button("📊 Analisar", key=f"v_{did}"):
                # 1. Carrega os dados do disco para a memória
                st.session_state['temp_df'] = ext_get_dataset(db, did)
                
                # 2. Avisa o sistema para mudar de página
                st.session_state['force_page'] = "📊 Análise IA" 
                
                # 3. Força o recarregamento imediato
                st.rerun()
            
            if not is_guest and c3.button("🤖 ML", key=f"m_{did}"):
                st.session_state['ml_target'] = did
                st.session_state['force_page'] = "🤖 ML Studio"
                st.rerun()

def render_ml_page(db):
    st.title("🤖 ML Studio")
    tid = st.session_state.get('ml_target')
    if not tid: st.warning("Seleciona um dataset."); return
    
    df = ext_get_dataset(db, tid)
    target = st.selectbox("Alvo", df.columns)
    
    if st.button("Treinar"):
        st.spinner("A treinar...")
        try:
            df_c = df.copy().dropna()
            for c in df_c.select_dtypes('object'): df_c[c] = LabelEncoder().fit_transform(df_c[c].astype(str))
            X = df_c.drop(columns=[target]); y = df_c[target]
            model = RandomForestRegressor() if (y.dtype!='object' and y.nunique()>10) else RandomForestClassifier()
            model.fit(X, y)
            msg = f"Modelo treinado para '{target}'"
            ext_send_notification(db, db.username, msg)
            st.success(msg)
        except Exception as e: st.error(str(e))

def main():
    if "authenticated" not in st.session_state: st.session_state["authenticated"] = False
    
    if not st.session_state["authenticated"]:
        render_login_page()
    else:
        user = st.session_state.get('username', 'system')
        is_guest = st.session_state.get('is_guest', False)
        
        # Carrega DB
        if is_guest and st.session_state.get("guest_viewing_owner"):
            db = HistoryManager(st.session_state["guest_viewing_owner"])
        else:
            db = HistoryManager(user)
            
        # Notificações
        real_db = HistoryManager(user) if is_guest else db
        notifs = real_db.full_db["users"].get(user, {}).get("notifications", [])
        unread = len([n for n in notifs if not n['read']])
        label_n = f"🔔 Notificações ({unread})" if unread > 0 else "🔔 Notificações"
        
        with st.sidebar:
            st.header("AInsight Pro")
            st.caption(f"User: {user}")
            
            # --- MENU SIMPLIFICADO (SEM DATA HUB) ---
            # Removemos "🧬 Data Hub" da lista
            opts = ["📊 Análise IA", "🤖 ML Studio", "🔨 Tarefas", "🧠 Docs", label_n]
            
            if not is_guest: opts.insert(2, "📨 Convites") # Insere Convites na posição 2
            opts.append("👤 Perfil")
            
            # Lógica de Redirecionamento
            if 'force_page' in st.session_state and st.session_state['force_page'] in opts:
                idx = opts.index(st.session_state['force_page']); del st.session_state['force_page']
            else: idx = 0
            
            page = st.radio("Menu", opts, index=idx)
            if st.button("Sair"): st.session_state.clear(); st.rerun()
            
        api_key = st.secrets.get("GEMINI_API_KEY", "")
        
        # O Roteador já não precisa do Data Hub
        if "Análise" in page: render_dashboard_classic(db, user, api_key)
        # elif "Data Hub" in page: render_data_hub(db)  <-- REMOVIDO
        elif "ML Studio" in page: render_ml_page(db)
        elif "Convites" in page: render_invites_page(db)
        elif "Tarefas" in page: render_tasks_page_simple(db)
        elif "Docs" in page: render_docs_page_simple(db)
        elif "Perfil" in page or "Notificações" in page: render_profile_page(real_db)

if __name__ == "__main__":
    main()