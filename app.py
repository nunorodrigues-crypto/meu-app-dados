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
    page_title="AInsight Pro", 
    page_icon="👁️", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- 2. MOTOR DE DADOS E BACKEND ---
HISTORY_FILE = "chat_database.json"

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

class HistoryManager:
    def __init__(self, username="system"):
        self.username = username
        self.load_db()

    def load_db(self):
        # AQUI ESTAVA O ERRO DE INDENTAÇÃO - AGORA ESTÁ CORRIGIDO
        if not os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'w') as f: json.dump({"users": {}, "guest_tokens": {}, "workspaces": {}}, f)
        try:
            with open(HISTORY_FILE, 'r') as f: self.full_db = json.load(f)
        except: self.full_db = {"users": {}, "guest_tokens": {}, "workspaces": {}}
        
        for k in ["users", "guest_tokens", "workspaces"]: 
            if k not in self.full_db: self.full_db[k] = {}

        if self.username not in self.full_db["users"]:
            self.user_data = {
                "chats": {}, "tasks": {}, "docs": {}, "datasets": {}, 
                "plan": "free", "workspaces": [], "notifications": [], "last_invite_at": None
            }
        else:
            self.user_data = self.full_db["users"][self.username]
            defaults = {"notifications": [], "last_invite_at": None, "chats": {}, "tasks": {}, "docs": {}, "datasets": {}}
            for k, v in defaults.items():
                if k not in self.user_data: self.user_data[k] = v

    def save_db(self):
        if self.username in self.full_db["users"]:
            self.full_db["users"][self.username] = self.user_data
            with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)

    # --- MÉTODOS DE DADOS ---
    def save_dataset_version(self, name, description, df, parent_id=None):
        if "datasets" not in self.user_data: self.user_data["datasets"] = {}
        djson = df.to_json(orient='split', date_format='iso')
        ts = datetime.now().isoformat()
        
        if parent_id and parent_id in self.user_data["datasets"]:
            ds = self.user_data["datasets"][parent_id]
            nv = f"v{len(ds['commits']) + 1}"
            ds["commits"].insert(0, {"version": nv, "message": description, "timestamp": ts, "author": self.username, "data_snapshot": djson})
            ds["current_version"] = nv
            ds_id = parent_id
        else:
            ds_id = str(uuid.uuid4())
            self.user_data["datasets"][ds_id] = {
                "name": name, "description": description, "created_at": ts, "owner": self.username, "current_version": "v1",
                "commits": [{"version": "v1", "message": "Init", "timestamp": ts, "author": self.username, "data_snapshot": djson}]
            }
        self.save_db()
        return ds_id

    def get_dataset(self, ds_id):
        if ds_id in self.user_data.get("datasets", {}):
            try: return pd.read_json(StringIO(self.user_data["datasets"][ds_id]["commits"][0]["data_snapshot"]), orient='split')
            except: return None
        return None

    # --- MÉTODOS CRUD ---
    def create_task(self, title, description="", priority="Média", assignee=None):
        tid = str(uuid.uuid4())
        self.user_data["tasks"][tid] = {"title": title, "description": description, "status": "To Do", "priority": priority, "assignee": assignee, "created_at": datetime.now().isoformat()}
        self.save_db()
    
    def move_task(self, tid, status):
        if tid in self.user_data["tasks"]: self.user_data["tasks"][tid]["status"] = status; self.save_db()
    
    def delete_task(self, tid):
        if tid in self.user_data["tasks"]: del self.user_data["tasks"][tid]; self.save_db()

    def create_doc(self, title, content=""):
        did = str(uuid.uuid4())
        self.user_data["docs"][did] = {"title": title, "content": content, "updated_at": datetime.now().isoformat()}
        self.save_db()
    
    def delete_doc(self, did):
        if did in self.user_data["docs"]: del self.user_data["docs"][did]; self.save_db()

# --- 3. FUNÇÕES AUXILIARES ---
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

def ext_send_notification(db, u, msg):
    db.load_db()
    if u in db.full_db["users"]:
        db.full_db["users"][u].setdefault("notifications", []).insert(0, {"msg": msg, "read": False, "timestamp": datetime.now().isoformat()})
        with open(HISTORY_FILE, 'w') as f: json.dump(db.full_db, f, indent=4, default=str)

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

# --- 4. CÉREBRO IA ---
def ask_gemini(df, query, key, persona, *args, **kwargs):
    genai.configure(api_key=key)
    chosen_model = "gemini-pro"
    try:
        for m in genai.list_models():
            if 'flash' in m.name: chosen_model = m.name; break
    except: pass

    p_txt = f"Atue como {persona}. Analise o dataframe."
    summary = df.describe(include='all').to_string()
    prompt = f"""
    {p_txt}
    RESUMO DADOS: {summary}
    PERGUNTA: "{query}"
    REGRAS:
    1. Responda SÓ com código Python em blocos ```python```.
    2. Usa o dataframe 'df' (já existe). NÃO uses pd.read_csv.
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
        return f"Erro: {e}", None

def generate_insights(df, persona, key, *args, **kwargs):
    q = "Gera um relatório completo com gráficos e insights profundos."
    code = ask_gemini(df, q, key, persona)
    return q, code

# --- 5. INTERFACES ---
def ui_login():
    st.markdown("## 🔐 AInsight Pro")
    t1, t2 = st.tabs(["Entrar", "Criar Conta"])
    with t1:
        u = st.text_input("User"); p = st.text_input("Pass", type="password")
        if st.button("Entrar"):
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
        if st.button("Registar"):
            ok, msg = ext_register_user(nu, np, ne)
            if ok: st.success(msg)
            else: st.error(msg)

def ui_datahub(db):
    st.title("🧬 Data Hub")
    is_guest = st.session_state.get('guest')
    
    if not is_guest:
        with st.expander("➕ Novo Dataset"):
            up = st.file_uploader("Ficheiro", type=['csv', 'xlsx'])
            nm = st.text_input("Nome")
            if st.button("Guardar") and up:
                try:
                    df = pd.read_csv(up) if up.name.endswith('.csv') else pd.read_excel(up)
                    df = smart_clean_dataframe(df)
                    db.save_dataset_version(nm, "Upload", df)
                    st.success("Guardado!"); time.sleep(0.5); st.rerun()
                except Exception as e: st.error(f"Erro: {e}")
    
    if not db.user_data.get("datasets"): st.info("Vazio."); return
    
    for did, d in db.user_data["datasets"].items():
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.write(f"**{d['name']}** ({d['current_version']})")
            
            # --- O BOTÃO MÁGICO DE PONTE ---
            if c2.button("📊 Analisar", key=did):
                st.session_state['df'] = db.get_dataset(did)
                st.session_state['force_page'] = "📊 Análise IA" # <--- OBRIGA A MUDAR
                st.rerun()
                
            if not is_guest and c3.button("🤖 ML", key=f"m_{did}"):
                st.session_state['ml_target'] = did
                st.session_state['force_page'] = "🤖 ML Studio"
                st.rerun()

def ui_analysis(db, key):
    st.title("📊 Análise IA")
    df = st.session_state.get('df')
    if df is None: st.warning("Vai ao Data Hub e seleciona um ficheiro."); return
    
    c1, c2 = st.columns([3, 1])
    c1.success(f"Dados: {len(df)} linhas")
    persona = c2.selectbox("Persona", ["Data Scientist", "CFO", "CMO", "COO"])
    
    if st.button("🚀 Relatório Automático", type="primary"):
        if not key: st.error("Falta API Key")
        else:
            with st.spinner("A analisar..."):
                q, code = generate_insights(df, persona, key)
                txt, fig = execute_code(code, df)
                if txt: st.markdown(txt)
                if fig: st.pyplot(fig)
                db.create_doc(f"Relatório {persona}", txt)
                st.toast("Guardado!")
                with st.expander("Código"): st.code(code)

    if q := st.chat_input("Pergunta..."):
        with st.chat_message("user"): st.write(q)
        with st.chat_message("assistant"):
            code = ask_gemini(df, q, key, persona)
            txt, fig = execute_code(code, df)
            st.write(txt)
            if fig: st.pyplot(fig)

def ui_ml(db):
    st.title("🤖 ML Studio")
    if st.session_state.get('guest'): st.error("Sem permissão."); return
    
    tid = st.session_state.get('ml_target')
    if not tid: st.warning("Escolhe no Data Hub."); return
    
    df = db.get_dataset(tid)
    target = st.selectbox("Alvo", df.columns)
    if st.button("Treinar"):
        try:
            dfc = df.copy().dropna()
            for c in dfc.select_dtypes('object'): dfc[c] = LabelEncoder().fit_transform(dfc[c].astype(str))
            X = dfc.drop(columns=[target]); y = dfc[target]
            model = RandomForestRegressor() if (y.dtype!='object' and y.nunique()>10) else RandomForestClassifier()
            model.fit(X, y)
            msg = f"Modelo treinado para '{target}'"
            ext_send_notification(db, db.username, msg)
            st.success(msg)
        except Exception as e: st.error(str(e))

def ui_invites(db):
    st.title("📨 Convites")
    with st.form("inv"):
        nm = st.text_input("Nome"); em = st.text_input("Email"); ph = st.text_input("Tel")
        perm = st.checkbox("Partilhar Dados")
        if st.form_submit_button("Enviar"):
            ok, res = ext_create_invite(db, db.username, {"email":em}, perm)
            if ok: st.success(f"Código: {res}"); st.toast("Simulação: Email enviado.")
            else: st.error(res)

def ui_profile(db):
    st.title("Perfil"); u = db.user_data
    st.info(f"User: {db.username}")
    if st.button("Limpar Notificações"): 
        u["notifications"] = []; db.save_db(); st.rerun()
    for n in u.get("notifications", []): st.write(f"📩 {n['msg']}")

def ui_tasks(db):
    st.title("Tarefas")
    with st.form("nt"):
        t = st.text_input("Tarefa")
        if st.form_submit_button("Add"): db.create_task(t); st.rerun()
    for k, v in db.user_data.get("tasks", {}).items(): st.write(f"- {v['title']}")

def ui_docs(db):
    st.title("Docs")
    for k, v in db.user_data.get("docs", {}).items():
        with st.expander(v['title']): st.write(v['content'])

# --- MAIN ---
def main():
    if "auth" not in st.session_state: st.session_state["auth"] = False
    
    if not st.session_state["auth"]:
        ui_login()
    else:
        user = st.session_state["user"]
        guest = st.session_state.get('guest', False)
        
        if guest and st.session_state.get("guest_viewing_owner"):
            db = HistoryManager(st.session_state["guest_viewing_owner"])
        else:
            db = HistoryManager(user)
            
        real_db = HistoryManager(user) if guest else db
        notifs = len(real_db.full_db["users"].get(user, {}).get("notifications", []))
        lbl_n = f"🔔 ({notifs})" if notifs > 0 else "🔔"
        
        with st.sidebar:
            st.header("AInsight Pro"); st.caption(f"User: {user}")
            opts = ["📊 Análise IA", "🧬 Data Hub", "🤖 ML Studio", "🔨 Tarefas", "🧠 Docs", lbl_n]
            if not guest: opts.insert(3, "📨 Convites")
            opts.append("👤 Perfil")
            
            # NAVEGAÇÃO FORÇADA (A PONTE CORRIGIDA)
            idx = 0
            if 'force_page' in st.session_state and st.session_state['force_page'] in opts:
                idx = opts.index(st.session_state['force_page']); del st.session_state['force_page']
            
            page = st.radio("Menu", opts, index=idx)
            if st.button("Sair"): st.session_state.clear(); st.rerun()
            
        key = st.secrets.get("GEMINI_API_KEY", "")
        
        if "Análise" in page: ui_analysis(db, key)
        elif "Data Hub" in page: ui_datahub(db)
        elif "ML Studio" in page: ui_ml(db)
        elif "Convites" in page: ui_invites(db)
        elif "Tarefas" in page: ui_tasks(db)
        elif "Docs" in page: ui_docs(db)
        elif "Perfil" in page or "Notificações" in page: ui_profile(real_db)

if __name__ == "__main__":
    main()