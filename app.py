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
from datetime import datetime
import requests
import qrcode
import urllib.parse
from streamlit_oauth import OAuth2Component
import numpy as np # Importação explícita no topo

# --- 1. CONFIGURAÇÃO GERAL ---
st.set_page_config(
    page_title="AInsight", 
    page_icon="👁️", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- 2. GESTOR DE BASE DE DADOS (JSON) ---
HISTORY_FILE = "chat_database.json"

class HistoryManager:
    def __init__(self, username="system"):
        self.username = username
        self.load_db()

    def load_db(self):
        # Se não existir ficheiro, cria estrutura base
        if not os.path.exists(HISTORY_FILE):
            init_db = {
                "users": {}, 
                "guest_tokens": {}, 
                "workspaces": {}
            }
            with open(HISTORY_FILE, 'w') as f:
                json.dump(init_db, f)
        
        # Carregar dados
        with open(HISTORY_FILE, 'r') as f:
            self.full_db = json.load(f)
        
        # Garantir integridade da estrutura
        if "workspaces" not in self.full_db: self.full_db["workspaces"] = {}
        if "guest_tokens" not in self.full_db: self.full_db["guest_tokens"] = {}
        
        # Criar user se não existir
        if self.username not in self.full_db["users"]:
            self.full_db["users"][self.username] = {
                "chats": {}, 
                "plan": "free", 
                "workspaces": []
            }
        
        self.user_data = self.full_db["users"][self.username]
        self.user_chats = self.user_data["chats"]

    def save_db(self):
        self.full_db["users"][self.username] = self.user_data
        with open(HISTORY_FILE, 'w') as f:
            json.dump(self.full_db, f, indent=4, default=str)

    # --- GESTÃO DE TOKENS (CONVITES) ---
    def create_one_time_token(self):
        token = str(uuid.uuid4())[:6].upper()
        self.full_db["guest_tokens"][token] = {
            "created_at": datetime.now().isoformat(),
            "used": False,
            "created_by": self.username
        }
        with open(HISTORY_FILE, 'w') as f:
            json.dump(self.full_db, f, indent=4, default=str)
        return token
    
    def validate_and_consume_token(self, token):
        token = token.strip().upper()
        tokens = self.full_db.get("guest_tokens", {})
        if token in tokens:
            if not tokens[token]["used"]:
                # Token válido -> Queimar token
                tokens[token]["used"] = True
                tokens[token]["used_at"] = datetime.now().isoformat()
                with open(HISTORY_FILE, 'w') as f:
                    json.dump(self.full_db, f, indent=4, default=str)
                return True
        return False

    # --- GESTÃO DE CHATS ---
    def create_chat(self, first_message, workspace_id=None):
        chat_id = str(uuid.uuid4())
        title = first_message[:30] + "..." if len(first_message) > 30 else first_message
        
        chat_obj = {
            "title": title, 
            "created_at": datetime.now().isoformat(), 
            "pinned": False, 
            "messages": [], 
            "notes": "", 
            "owner": self.username, 
            "shared_with": [], 
            "workspace_id": workspace_id
        }
        
        # Se for workspace, guarda na estrutura do workspace
        if workspace_id and workspace_id in self.full_db["workspaces"]:
            self.full_db["workspaces"][workspace_id]["chats"][chat_id] = chat_obj
            with open(HISTORY_FILE, 'w') as f:
                json.dump(self.full_db, f, indent=4, default=str)
        else:
            self.user_chats[chat_id] = chat_obj
            self.save_db()
            
        return chat_id

    def get_chat(self, chat_id):
        # 1. Procurar nos meus chats pessoais
        if chat_id in self.user_chats:
            return self.user_chats[chat_id]
        
        # 2. Procurar em chats partilhados comigo (User a User)
        for u_email, u_data in self.full_db["users"].items():
            if chat_id in u_data["chats"]:
                if self.username in u_data["chats"][chat_id].get("shared_with", []):
                    return u_data["chats"][chat_id]
        
        # 3. Procurar em Workspaces
        for wid, wdata in self.full_db["workspaces"].items():
            if chat_id in wdata["chats"]:
                # Se sou membro ou dono, posso ver
                if self.username in wdata["members"] or self.username == wdata["owner"]:
                    return wdata["chats"][chat_id]
        return None

    def update_chat(self, chat_id, chat_data):
        # Atualizar Pessoal
        if chat_id in self.user_chats:
            self.user_chats[chat_id] = chat_data
            self.save_db()
            return
        
        # Atualizar Workspace
        for wid, wdata in self.full_db["workspaces"].items():
            if chat_id in wdata["chats"]:
                self.full_db["workspaces"][wid]["chats"][chat_id] = chat_data
                with open(HISTORY_FILE, 'w') as f:
                    json.dump(self.full_db, f, indent=4, default=str)
                return
        
        # Atualizar Partilhado
        for u_email, u_data in self.full_db["users"].items():
             if chat_id in u_data["chats"]:
                 self.full_db["users"][u_email]["chats"][chat_id] = chat_data
                 with open(HISTORY_FILE, 'w') as f:
                    json.dump(self.full_db, f, indent=4, default=str)
                 return

    def share_chat(self, chat_id, target_email):
        chat = self.get_chat(chat_id)
        if chat:
            if target_email not in chat["shared_with"]:
                chat["shared_with"].append(target_email)
                self.update_chat(chat_id, chat)
            return True
        return False
    
    def delete_chat(self, chat_id):
        # Apagar Pessoal
        if chat_id in self.user_chats:
            del self.user_chats[chat_id]
            self.save_db()
            return True
        
        # Apagar Workspace (se for dono)
        for wid, wdata in self.full_db["workspaces"].items():
            if chat_id in wdata["chats"] and wdata["owner"] == self.username:
                del wdata["chats"][chat_id]
                with open(HISTORY_FILE, 'w') as f:
                    json.dump(self.full_db, f, indent=4, default=str)
                return True
        return False

    # --- GESTÃO DE WORKSPACES ---
    def upgrade_plan(self):
        self.user_data["plan"] = "pro"
        self.save_db()

    def create_workspace(self, name):
        if self.user_data["plan"] != "pro":
            return False, "Requer Plano PRO"
        
        ws_id = str(uuid.uuid4())
        self.full_db["workspaces"][ws_id] = {
            "name": name, 
            "owner": self.username, 
            "members": [self.username], 
            "chats": {}
        }
        self.user_data["workspaces"].append(ws_id)
        with open(HISTORY_FILE, 'w') as f:
            json.dump(self.full_db, f, indent=4, default=str)
        return True, "Criado"

    def add_member_to_workspace(self, ws_id, email):
        if ws_id in self.full_db["workspaces"]:
            ws = self.full_db["workspaces"][ws_id]
            if email not in ws["members"]:
                ws["members"].append(email)
                # Criar user fantasma se não existir para guardar a referencia
                if email in self.full_db["users"]:
                    if ws_id not in self.full_db["users"][email].get("workspaces", []):
                         self.full_db["users"][email].setdefault("workspaces", []).append(ws_id)
                
                with open(HISTORY_FILE, 'w') as f:
                    json.dump(self.full_db, f, indent=4, default=str)
                return True
        return False

# --- 3. FUNÇÕES DE PROCESSAMENTO DE DADOS ---
def convert_currency_to_float(val):
    """
    Blindagem: Converte strings de moeda (ex: '1.200,50 €') para float (1200.50).
    Remove erros comuns de formatação europeia vs americana.
    """
    if isinstance(val, (int, float)): return val
    if pd.isna(val) or val == '': return 0.0
    
    val = str(val).strip()
    # Remove símbolos de moeda comuns e espaços
    val = re.sub(r'[€R$£¥ ]', '', val)
    
    try:
        # Detetar formato Europeu/Brasil (1.000,00) vs Americano (1,000.00)
        if ',' in val and '.' in val:
            if val.find('.') < val.find(','): # Estilo 1.200,50
                val = val.replace('.', '').replace(',', '.')
            else: # Estilo 1,200.50
                val = val.replace(',', '')
        elif ',' in val: # Apenas vírgula (1200,50) -> troca por ponto
            val = val.replace(',', '.')
            
        return float(val)
    except:
        return 0.0

def smart_clean_dataframe(df):
    """
    Blindagem: Percorre o Excel e corrige automaticamente Datas e Dinheiro
    antes que a IA possa cometer erros.
    """
    # 1. Remover Linhas Vazias
    df.dropna(how='all', inplace=True)
    
    # 2. Normalizar Datas (Tenta converter tudo o que parece data)
    for col in df.columns:
        if df[col].dtype == 'object':
            try:
                df[col] = pd.to_datetime(df[col])
            except:
                pass

    # 3. Normalizar Dinheiro (Deteta colunas com números misturados com texto)
    for col in df.columns:
        if df[col].dtype == 'object':
            # Verifica numa amostra se existem dígitos
            sample = df[col].astype(str).head(10).tolist()
            if any(c.isdigit() for s in sample for c in s):
                try:
                    # Aplica a blindagem de moeda
                    df[col] = df[col].apply(convert_currency_to_float)
                except:
                    pass
    return df

# --- FUNÇÕES ORIGINAIS MODIFICADAS ---

def clean_individual_df(df, filename):
    """Limpa dados com a nova blindagem e normaliza datas."""
    
    # APLICA A BLINDAGEM AQUI
    df = smart_clean_dataframe(df)
    
    df.drop_duplicates(inplace=True)
    date_col = None
    
    # Tenta detetar coluna de data (agora mais fiável porque o smart_clean já correu)
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            date_col = col
            break
            
    if not date_col:
        for col in df.columns:
            if df[col].dtype == 'object':
                try:
                    df[col] = pd.to_datetime(df[col])
                    date_col = col
                    break
                except: pass
    
    if date_col:
        df.rename(columns={date_col: 'DATA_FUSAO'}, inplace=True)
        return df, True
    return df, False

def load_from_url(url):
    """Lê Google Sheets ou CSVs online."""
    try:
        if "docs.google.com" in url:
            url = url.replace("/edit?usp=sharing", "/export?format=csv").replace("/edit", "/export?format=csv")
        
        response = requests.get(url)
        response.raise_for_status()
        
        try: 
            return pd.read_csv(StringIO(response.text)), "Link_CSV"
        except: 
            return pd.read_excel(BytesIO(response.content)), "Link_Excel"
    except Exception as e:
        return None, str(e)

def smart_merge(files=None, url_df=None, url_name=None):
    """Funde múltiplos ficheiros numa Super Tabela."""
    dataframes = []
    file_names = []
    
    # 1. Processar Uploads de Ficheiros
    if files:
        for f in files:
            try:
                # Ler o ficheiro
                if f.name.endswith('.csv'): 
                    df = pd.read_csv(f)
                else: 
                    df = pd.read_excel(f)
                
                # --- A CORREÇÃO ESTÁ AQUI EM BAIXO ---
                # Temos de separar o resultado em 2 variáveis (Tabela, Data)
                clean_df, has_date = clean_individual_df(df, f.name)
                
                if has_date:
                    prefix = f.name.split('.')[0]
                    clean_df.columns = [f"{prefix}_{c}" if c != 'DATA_FUSAO' else 'DATA_FUSAO' for c in clean_df.columns]
                    dataframes.append(clean_df)
                    file_names.append(f.name)
                else:
                    dataframes.append(clean_df)
                    file_names.append(f.name)
            except Exception as e:
                # Se falhar a ler um ficheiro específico, ignora e continua
                print(f"Erro a ler {f.name}: {e}")

    # 2. Processar Link Cloud
    if url_df is not None:
        try:
            clean_df, has_date = clean_individual_df(url_df, url_name)
            if has_date:
                clean_df.columns = [f"CLOUD_{c}" if c != 'DATA_FUSAO' else 'DATA_FUSAO' for c in clean_df.columns]
            dataframes.append(clean_df)
            file_names.append(url_name)
        except: pass

    # 3. Verificações Finais
    if not dataframes:
        return None, "Sem dados válidos."

    try:
        # Se só houver 1 ficheiro, devolve logo a tabela limpa
        if len(dataframes) == 1:
            return dataframes[0], file_names
        
        # Se houver vários, tenta fundir
        all_have_date = all(['DATA_FUSAO' in df.columns for df in dataframes])
        
        if all_have_date:
            df_final = reduce(lambda l,r: pd.merge(l, r, on='DATA_FUSAO', how='outer'), dataframes)
            return df_final.sort_values('DATA_FUSAO').fillna(0), file_names
        else:
            df_final = pd.concat([d.reset_index(drop=True) for d in dataframes], axis=1)
            return df_final.fillna(0), file_names

    except Exception as e:
        return None, f"Erro na fusão: {e}"

# --- 4. CÉREBRO DE IA (GEMINI) ---
def ask_gemini(df, query, api_key, context, file_list, persona):
    genai.configure(api_key=api_key)
    
    # 1. Seleção de Modelo
    chosen_model = "gemini-pro"
    try:
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if 'models/gemini-1.5-pro' in models: chosen_model = 'models/gemini-1.5-pro'
    except: pass
    
    model = genai.GenerativeModel(chosen_model)
    
    # 2. Personas com Foco "Blindado"
    personas_prompts = {
        "Data Scientist": "Atue como Data Scientist Senior. Seja técnico, preciso e procure correlações estatísticas.",
        "CFO (Financeiro)": "Atue como CFO. Foque EXCLUSIVAMENTE em métricas financeiras (Receita, Custo, Margem, Lucro). Ignore métricas de vaidade.",
        "CMO (Marketing)": "Atue como CMO. Foque em Conversão, CAC, ROAS e Canais de Aquisição.",
        "COO (Operacional)": "Atue como COO. Foque em Eficiência, Volume de Pedidos, Prazos e Logística."
    }
    p_txt = personas_prompts.get(persona, "Atue como Analista de Dados.")
    
    # 3. EXTRAÇÃO DE METADADOS (Para a IA não alucinar colunas)
    # Cria uma lista limpa tipo: "- valor_venda (float64)"
    columns_info = "\n".join([f"- {col} ({dtype})" for col, dtype in df.dtypes.items()])

    # 4. PROMPT DE ENGENHARIA ESTRITA
    prompt = f"""
    {p_txt}
    
    CONTEXTO DO NEGÓCIO: {context}
    
    --- DADOS DISPONÍVEIS (DATAFRAME 'df') ---
    O dataframe 'df' JÁ ESTÁ LIMPO e carregado em memória.
    As colunas exatas disponíveis são:
    {columns_info}
    
    PERGUNTA DO UTILIZADOR: "{query}"
    
    --- REGRAS DE OURO (PYTHON) ---
    1. USE APENAS AS COLUNAS LISTADAS ACIMA. Não invente nomes.
    2. NÃO use pd.read_csv(). Use a variável 'df' diretamente.
    3. Importe sempre: import pandas as pd; import matplotlib.pyplot as plt; import seaborn as sns; import numpy as np
    4. Valores monetários JÁ SÃO FLOAT. Não tente limpar strings com .replace(). Apenas calcule.
    5. Gráficos: Use plt.figure(figsize=(10,6)) antes de plotar. Use sns.barplot, sns.lineplot, etc.
    6. Responda APENAS com código Python executável dentro de blocos ```python ... ```.
    """
    
    try:
        response = model.generate_content(prompt)
        
        # Extração segura do código
        match = re.search(r"```python(.*?)```", response.text, re.DOTALL)
        if match:
            return match.group(1).strip()
        else:
            # Fallback: Se a IA esquecer os backticks, tenta usar o texto se parecer código
            clean_text = response.text.replace("```", "").strip()
            if "plt." in clean_text or "print" in clean_text:
                return clean_text
            return f"print('A IA não gerou código válido. Resposta: {clean_text}')"
            
    except Exception as e:
        return f"print('Erro crítico na IA: {e}')"
    
    # Prompt com IMPORT OBRIGATÓRIO para corrigir erro 'pd not defined'
    
    prompt = f"""
    {persona_text}
    
    CONTEXTO DE NEGÓCIO: {context}
    NOMES DOS FICHEIROS CARREGADOS: {', '.join(file_list)}
    
    ESTRUTURA DOS DADOS (DataFrame 'df'):
    {df.dtypes.to_string()}
    
    PERGUNTA DO UTILIZADOR: "{query}"
    
    REGRAS OBRIGATÓRIAS (CRÍTICO):
    1. NÃO use pd.read_csv() nem pd.read_excel(). Os ficheiros NÃO estão no disco.
    2. Os dados JÁ estão carregados na memória na variável 'df'. Use APENAS 'df'.
    3. Comece sempre com os imports: import pandas as pd; import matplotlib.pyplot as plt; import seaborn as sns; import numpy as np
    4. Use print() para escrever a resposta de texto.
    5. Use plt.figure() para criar gráficos.
    """
    
    # ... (o resto da função continua igual) ...
    
    try:
        response = model.generate_content(prompt)
        # Limpeza do código
        match = re.search(r"```python(.*?)```", response.text, re.DOTALL)
        return match.group(1).strip() if match else response.text.replace("```", "").strip()
    except Exception as e:
        return f"print('Erro na IA: {e}')"

def execute_code(code, df):
    try:
        import matplotlib.pyplot as plt
        import seaborn as sns
        
        plt.clf(); plt.close('all') 
        
        # 1. FILTRO DE SEGURANÇA (BLACKLIST)
        dangerous = ["os.", "sys.", "subprocess", "open(", "delete", "rm -rf", "import os", "import sys", "__import__"]
        for word in dangerous:
            if word in code:
                return "⚠️ BLOQUEADO: Tentativa de código não seguro detetada.", None

        old_stdout = sys.stdout
        redirected_output = sys.stdout = StringIO()
        
        # 2. SANDBOX (Só permite estas ferramentas)
        safe_locals = {'df': df, 'pd': pd, 'plt': plt, 'sns': sns, 'np': np, 're': re}
        
        # Executa sem acesso aos comandos do sistema
        exec(code, {"__builtins__": {}}, safe_locals)
        
        sys.stdout = old_stdout
        text_output = redirected_output.getvalue()
        
        fig = plt.gcf()
        if not plt.gca().has_data(): fig = None
        
        return text_output, fig

    except Exception as e:
        sys.stdout = sys.__stdout__
        return f"❌ Erro de Execução: {str(e)}", None

def generate_role_insights(df, persona, api_key, context, file_list):
    # Perguntas automáticas por cargo
    queries = {
        "CFO (Financeiro)": "Resumo Financeiro: Total Receitas, Custos e Margens ao longo do tempo.",
        "CMO (Marketing)": "Resumo Marketing: Top Produtos/Canais e Tendências de Vendas.",
        "COO (Operacional)": "Resumo Operacional: Volume total de pedidos, picos de carga por data e status.",
        "Data Scientist": "Análise Técnica: df.describe(), nulos e correlações."
    }
    query = queries.get(persona, "Resumo Geral")
    code = ask_gemini(df, query, api_key, context, file_list, persona)
    return query, code

def create_pdf(chat_data):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt=f"Relatorio: {chat_data.get('title', 'Analise')}", ln=1, align='C')
    pdf.ln(10)
    
    # Secção de Notas
    if chat_data.get("notes"):
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, txt="NOTAS", ln=1)
        pdf.set_font("Arial", size=10)
        clean_notes = chat_data.get("notes", "").replace("€", "EUR").encode('latin-1', 'replace').decode('latin-1')
        pdf.multi_cell(0, 10, txt=clean_notes)
        pdf.ln(10)
    
    # Secção de Chat
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, txt="HISTORICO", ln=1)
    pdf.set_font("Arial", size=10)
    
    for msg in chat_data.get("messages", []):
        role_title = "IA" if msg['role'] == "assistant" else "UTILIZADOR"
        clean_text = msg["content"].replace("€", "EUR").encode('latin-1', 'replace').decode('latin-1')
        
        pdf.set_font("Arial", 'B', 10)
        pdf.cell(0, 10, txt=f"[{role_title}]", ln=1)
        
        pdf.set_font("Arial", size=10)
        pdf.multi_cell(0, 10, txt=clean_text)
        pdf.ln(5)
        
    return pdf.output(dest='S').encode('latin-1')

# --- 5. UTILITÁRIOS VISUAIS ---
def generate_qr_code(data):
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    buf = BytesIO()
    img.save(buf)
    return buf.getvalue()

def generate_whatsapp_link(text):
    return f"https://wa.me/?text={urllib.parse.quote(text)}"

def generate_mailto_link(email, subject, body):
    return f"mailto:{email}?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body)}"

# --- 6. PÁGINAS DA APLICAÇÃO ---

def login_page():
    # CSS Fundo Animado
    st.markdown("""
        <style>
        [data-testid="stAppViewContainer"] {
            background: linear-gradient(-45deg, #0f0c29, #302b63, #24243e);
            background-size: 400% 400%;
            animation: gradient 15s ease infinite;
            color: white;
        }
        @keyframes gradient {
            0% {background-position: 0% 50%;}
            50% {background-position: 100% 50%;}
            100% {background-position: 0% 50%;}
        }
        h1, p, label, .stMarkdown { color: white !important; }
        </style>
    """, unsafe_allow_html=True)

    # Auto-login por token
    if "token" in st.query_params:
        tk_url = st.query_params["token"]
        db = HistoryManager()
        if db.validate_and_consume_token(tk_url):
            st.session_state['authenticated'] = True
            st.session_state['username'] = "Convidado"
            st.session_state['is_guest'] = True
            st.success("Token válido! A entrar...")
            time.sleep(1)
            st.rerun()

    # Layout Central
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        # LOGO (Placeholder ou ficheiro local)
        st.image("https://cdn-icons-png.flaticon.com/512/8637/8637099.png", width=100)
        st.markdown("<h1 style='text-align: center; margin-top:-20px'>AInsight</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; opacity: 0.7'>Business Intelligence AI</p>", unsafe_allow_html=True)
        st.write("") 

        # Login Clássico
        with st.form("login_form"):
            u = st.text_input("Utilizador")
            p = st.text_input("Password", type="password")
            if st.form_submit_button("Entrar", use_container_width=True):
                ru = st.secrets.get("ADMIN_USER", "admin")
                rp = st.secrets.get("ADMIN_PASSWORD", "123")
                if u == ru and p == rp:
                    st.session_state['authenticated'] = True
                    st.session_state['username'] = u
                    st.session_state['is_guest'] = False
                    st.rerun()
                else:
                    st.error("Credenciais inválidas.")
        
        st.markdown("<div style='text-align: center; margin: 15px;'>ou</div>", unsafe_allow_html=True)
        
        # Login Google
        if "GOOGLE_CLIENT_ID" in st.secrets:
            try:
                oauth2 = OAuth2Component(
                    st.secrets["GOOGLE_CLIENT_ID"], 
                    st.secrets["GOOGLE_CLIENT_SECRET"], 
                    "https://accounts.google.com/o/oauth2/v2/auth", 
                    "https://oauth2.googleapis.com/token", 
                    "https://www.googleapis.com/oauth2/v1/tokeninfo", 
                    "https://www.googleapis.com/oauth2/v1/userinfo"
                )
                res = oauth2.authorize_button(
                    name="Entrar com Google", 
                    icon="https://www.google.com.tw/favicon.ico", 
                    redirect_uri=st.secrets["GOOGLE_REDIRECT_URI"], 
                    scope="email profile", 
                    key="google_login_btn"
                )
                if res and "token" in res:
                    st.session_state['authenticated'] = True
                    st.session_state['username'] = "Google User"
                    st.session_state['is_guest'] = False
                    st.rerun()
            except Exception as e:
                st.warning("Configuração Google incompleta.")
            
        st.write("")
        
        # Login Código Convidado
        with st.expander("🎟️ Tenho um Código de Acesso"):
            tk = st.text_input("Código de 6 dígitos")
            if st.button("Validar Código", key="validate_token_btn", use_container_width=True):
                db = HistoryManager()
                if db.validate_and_consume_token(tk):
                    st.session_state['authenticated'] = True
                    st.session_state['username'] = "Convidado"
                    st.session_state['is_guest'] = True
                    st.rerun()
                else:
                    st.error("Código inválido ou já usado.")

def main_app():
    user = st.session_state.get('username', 'User')
    is_guest = st.session_state.get('is_guest', False)
    db = HistoryManager(user)

    # --- BARRA LATERAL (SIDEBAR) ---
    with st.sidebar:
        # Logo e Info User
        c_logo, c_txt = st.columns([1, 3])
        with c_logo: 
            st.image("https://cdn-icons-png.flaticon.com/512/8637/8637099.png", width=50)
        with c_txt: 
            st.markdown("### AInsight")
            st.caption(f"Logado como: {user}")

        # Área Admin: Gerar Convites
        if not is_guest:
            with st.expander("🎟️ Gerar Convite", expanded=False):
                if st.button("Criar Novo Código", key="gen_token_btn"):
                    tk = db.create_one_time_token()
                    base_url = st.secrets.get("APP_URL", "#")
                    magic_link = f"{base_url}?token={tk}"
                    
                    st.success(f"Código: {tk}")
                    st.image(generate_qr_code(magic_link), width=150, caption="QR de Acesso")
                    
                    wa_msg = f"Olá! Acede à AInsight aqui: {magic_link} \nOu usa o código: *{tk}*"
                    wa_url = generate_whatsapp_link(wa_msg)
                    st.markdown(f"[📲 Enviar WhatsApp]({wa_url})")

        st.markdown("---")
        
        # Seletor de Modo (Pessoal vs Empresa)
        context_mode = st.radio("Modo:", ["Pessoal", "Workspaces"], horizontal=True)
        selected_ws_id = None
        
        if context_mode == "Workspaces":
            if db.user_data["plan"] != "pro":
                st.info("Funcionalidade PRO")
                if st.button("💎 Upgrade"): 
                    db.upgrade_plan()
                    st.rerun()
            else:
                # Listar Workspaces
                my_ws = {k:v for k,v in db.full_db["workspaces"].items() if user in v["members"]}
                if my_ws:
                    selected_ws_id = st.selectbox("Empresa", list(my_ws.keys()), format_func=lambda x: my_ws[x]["name"])
                
                # Criar Workspace
                with st.popover("🏢 Nova Empresa"):
                    n = st.text_input("Nome")
                    if st.button("Criar"): 
                        db.create_workspace(n)
                        st.rerun()

        st.markdown("---")
        
        # Botão Nova Análise
        if st.button("➕ Nova Análise", use_container_width=True, key="new_chat_btn"):
            st.session_state['current_chat_id'] = None
            st.rerun()
        
        # Lista de Chats
        chats_source = db.user_chats
        if context_mode == "Workspaces" and selected_ws_id:
             chats_source = db.full_db["workspaces"][selected_ws_id]["chats"]

        st.caption("HISTÓRICO")
        for cid, d in sorted(chats_source.items(), key=lambda x:x[1]['created_at'], reverse=True):
            c1, c2 = st.columns([1, 5])
            with c1: 
                if st.button("🗑️", key=f"del_{cid}"): 
                    db.delete_chat(cid)
                    if st.session_state.get('current_chat_id') == cid: 
                        st.session_state['current_chat_id'] = None
                    st.rerun()
            with c2:
                if st.button(f"💬 {d['title']}", key=cid): 
                    st.session_state['current_chat_id'] = cid
                    st.rerun()
        
        st.markdown("---")
        if st.button("🚪 Sair", key="logout_btn"): 
            st.session_state['authenticated'] = False
            st.query_params.clear()
            st.rerun()

    # --- ÁREA CENTRAL (MAIN) ---
    current_id = st.session_state.get('current_chat_id')
    
    # Inicializar variáveis de sessão
    if 'temp_df' not in st.session_state: st.session_state['temp_df'] = None
    if 'temp_files' not in st.session_state: st.session_state['temp_files'] = []

    # CENÁRIO 1: CONFIGURAÇÃO DE NOVA ANÁLISE
    if current_id is None:
        st.title("✨ Nova Análise")
        st.markdown("Carregue os seus dados para começar a explorar.")
        
        # Input de API Key (se não estiver nos segredos)
        if "GEMINI_API_KEY" in st.secrets: 
            api_key = st.secrets["GEMINI_API_KEY"]
        else: 
            api_key = st.text_input("Insira a sua Gemini API Key", type="password")
        
        # Configuração
        c1, c2 = st.columns(2)
        persona = c1.selectbox("Persona (Quem analisa?)", ["Data Scientist", "CFO (Financeiro)", "CMO (Marketing)", "COO (Operacional)"])
        context = c2.text_area("Contexto do Negócio", height=40, placeholder="Ex: E-commerce de moda...")
        
        # Upload
        t1, t2 = st.tabs(["📂 Upload Ficheiros", "🔗 Link Cloud"])
        
        up_files = t1.file_uploader("Arraste ficheiros (Excel/CSV)", accept_multiple_files=True)
        
        url_df = None
        url_name = None
        url_input = t2.text_input("Link do Google Sheets (Público)")
        if url_input: 
            url_df, url_name = load_from_url(url_input)
        
# Processamento Automático
        if up_files or url_df is not None:
            # 1. Tenta fundir os ficheiros (Chamada única!)
            result = smart_merge(up_files, url_df, url_name)
            
            # 2. Garante que o resultado é válido antes de avançar
            if result is None:
                 st.error("Erro desconhecido no processamento dos ficheiros.")
            else:
                df, fn = result

                # 3. VERIFICAÇÃO DE SEGURANÇA: Só avança se df for uma Tabela real (DataFrame)
                # Isto impede o erro 'AttributeError' que estavas a ter
                if df is not None and isinstance(df, pd.DataFrame):
                    st.success(f"✅ {len(fn)} Fontes de Dados Conectadas!")
                    st.session_state['temp_df'] = df
                    st.session_state['temp_files'] = fn
                    
                    # --- BOTÃO RELATÓRIO AUTOMÁTICO ---
                    if st.button(f"🚀 Relatório Automático ({persona})", use_container_width=True):
                        if not api_key:
                            st.error("Falta API Key")
                        else:
                            new_id = db.create_chat(f"Relatório Auto: {persona}", workspace_id=selected_ws_id)
                            with st.spinner("A gerar relatório..."):
                                q, code = generate_role_insights(df, persona, api_key, context, fn)
                                txt, fig = execute_code(code, df)
                                
                                c_data = db.get_chat(new_id)
                                c_data["messages"].extend([{"role": "user", "content": q}, {"role": "assistant", "content": txt}])
                                db.update_chat(new_id, c_data)
                                
                                st.session_state['current_chat_id'] = new_id
                                st.rerun()
                    # ----------------------------------------

                    # Visualizar Dados (Só aparece se a tabela for válida)
                    with st.expander("Visualizar Dados"):
                        st.dataframe(df.head())
                
                # Se o df não for uma tabela (ex: erro na leitura), mostra o erro
                else:
                    st.error(f"Não foi possível ler os dados. Detalhe: {fn}")
        
        # Caixa de Pergunta Inicial (Manual) - Só aparece se já houver dados carregados
        if st.session_state.get('temp_df') is not None:
            if query := st.chat_input("O que gostaria de saber sobre estes dados?"):
                if not api_key:
                    st.error("Por favor configure a API Key primeiro.")
                else:
                    # Criar Chat e Redirecionar
                    new_id = db.create_chat(query, workspace_id=selected_ws_id)
                    
                    with st.spinner(f"O {persona} está a analisar..."):
                        code = ask_gemini(st.session_state['temp_df'], query, api_key, context, st.session_state['temp_files'], persona)
                        text, fig = execute_code(code, st.session_state['temp_df'])
                        
                        # Salvar mensagens
                        chat_data = db.get_chat(new_id)
                        chat_data["messages"].append({"role": "user", "content": query})
                        chat_data["messages"].append({"role": "assistant", "content": text})
                        db.update_chat(new_id, chat_data)
                        
                        # Entrar no chat
                        st.session_state['current_chat_id'] = new_id
                        st.rerun()
                # ----------------------------------------

                with st.expander("Visualizar Dados"):
                    st.dataframe(df.head())
            # ----------------------------------------

            with st.expander("Visualizar Dados"):
                st.dataframe(df.head())
        
        # Caixa de Pergunta Inicial
        if query := st.chat_input("O que gostaria de saber sobre estes dados?"):
            if not api_key or st.session_state['temp_df'] is None:
                st.error("Por favor configure a API Key e carregue Dados primeiro.")
            else:
                # Criar Chat e Redirecionar
                new_id = db.create_chat(query, workspace_id=selected_ws_id)
                
                with st.spinner(f"O {persona} está a analisar..."):
                    code = ask_gemini(st.session_state['temp_df'], query, api_key, context, st.session_state['temp_files'], persona)
                    text, fig = execute_code(code, st.session_state['temp_df'])
                    
                    # Salvar mensagens
                    chat_data = db.get_chat(new_id)
                    chat_data["messages"].append({"role": "user", "content": query})
                    chat_data["messages"].append({"role": "assistant", "content": text})
                    db.update_chat(new_id, chat_data)
                    
                    # Entrar no chat
                    st.session_state['current_chat_id'] = new_id
                    st.rerun()

    # CENÁRIO 2: DENTRO DE UMA ANÁLISE
    else:
        chat_data = db.get_chat(current_id)
        if not chat_data:
            st.error("Erro ao carregar chat.")
            st.session_state['current_chat_id'] = None
            st.rerun()
        
        # Cabeçalho e Partilha
        c1, c2 = st.columns([3, 1])
        with c1: 
            st.subheader(f"📂 {chat_data['title']}")
        with c2:
            with st.popover("📤 Partilhar"):
                em = st.text_input("Email do Colega")
                if st.button("Dar Acesso"):
                    if db.share_chat(current_id, em):
                        st.success("Partilhado!")
                        link = st.secrets.get("APP_URL", "#")
                        subject = f"Convite AInsight: {chat_data['title']}"
                        body = f"Olá, partilhei uma análise contigo. Acede aqui: {link}"
                        st.markdown(f"[📧 Enviar Email]({generate_mailto_link(em, subject, body)})")
                    else:
                        st.error("Erro ou já partilhado.")

        # Layout Dividido: Chat vs Notas
        col_chat, col_notes = st.columns([2, 1])
        
        with col_notes:
            st.markdown("### 📝 Notas")
            notes = st.text_area("Bloco de Notas", value=chat_data.get("notes", ""), height=500, key="notes_area")
            if notes != chat_data.get("notes", ""):
                chat_data["notes"] = notes
                db.update_chat(current_id, chat_data)
                st.toast("Notas salvas.")
        
        with col_chat:
            # Renderizar mensagens anteriores
            for msg in chat_data.get("messages", []):
                st.chat_message(msg["role"]).write(msg["content"])
            
            # Input de Nova Pergunta
            if query := st.chat_input("Continuar a análise..."):
                # Verificar se temos dados em memória
                df = st.session_state.get('temp_df')
                
                if df is None:
                    st.warning("⚠️ Sessão expirou. Por favor recarregue os dados na 'Nova Análise'.")
                else:
                    st.chat_message("user").write(query)
                    chat_data["messages"].append({"role": "user", "content": query})
                    
                    with st.spinner("A pensar..."):
                        # Usa contexto salvo ou padrão
                        ctx = context if 'context' in locals() else ""
                        prs = persona if 'persona' in locals() else "Data Scientist"
                        
                        code = ask_gemini(df, query, st.secrets["GEMINI_API_KEY"], ctx, st.session_state['temp_files'], prs)
                        text, fig = execute_code(code, df)
                        
                        st.chat_message("assistant").write(text)
                        if fig: st.chat_message("assistant").pyplot(fig)
                        
                        chat_data["messages"].append({"role": "assistant", "content": text})
                        db.update_chat(current_id, chat_data)
            
            # Botão PDF (só aparece se houver mensagens)
            if chat_data.get("messages"):
                st.markdown("---")
                pdf_bytes = create_pdf(chat_data)
                st.download_button("📄 Baixar Relatório PDF", pdf_bytes, "relatorio_ainsight.pdf", "application/pdf", key="btn_pdf")

if __name__ == "__main__":
    if "authenticated" not in st.session_state: 
        st.session_state["authenticated"] = False
    
    if st.session_state["authenticated"]: 
        main_app()
    else: 
        login_page()