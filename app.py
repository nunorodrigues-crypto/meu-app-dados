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

   # --- 2. GESTOR DE BASE DE DADOS (JSON) ---
HISTORY_FILE = "chat_database.json"

class HistoryManager:
    def __init__(self, username="system"):
        self.username = username
        self.load_db()

    def load_db(self):
        # 1. Se não existir ficheiro, cria estrutura base
        if not os.path.exists(HISTORY_FILE):
            init_db = {
                "users": {}, 
                "guest_tokens": {}, 
                "workspaces": {}
            }
            with open(HISTORY_FILE, 'w') as f:
                json.dump(init_db, f)
        
        # 2. Carregar dados
        with open(HISTORY_FILE, 'r') as f:
            self.full_db = json.load(f)
        
        # 3. Garantir integridade da estrutura Raiz
        defaults = ["workspaces", "guest_tokens"]
        for d in defaults:
            if d not in self.full_db: self.full_db[d] = {}
        
        # 4. Criar user se não existir
        if self.username not in self.full_db["users"]:
            self.full_db["users"][self.username] = {
                "chats": {}, 
                "tasks": {},     # <--- Gaveta para o Monday.com
                "docs": {},      # <--- Gaveta para o Notion
                "datasets": {},  # <--- Gaveta para o GitHub (Dados)
                "plan": "free", 
                "workspaces": []
            }
        
        self.user_data = self.full_db["users"][self.username]

        # 5. MIGRADOR AUTOMÁTICO
        required_keys = ["chats", "tasks", "docs", "datasets"]
        for key in required_keys:
            if key not in self.user_data:
                self.user_data[key] = {}
        
        self.user_chats = self.user_data["chats"]

    def save_db(self):
        self.full_db["users"][self.username] = self.user_data
        with open(HISTORY_FILE, 'w') as f:
            json.dump(self.full_db, f, indent=4, default=str)

    def save_db(self):
        # ... (O resto do teu código continua igual daqui para baixo)
        self.full_db["users"][self.username] = self.user_data
        with open(HISTORY_FILE, 'w') as f:
            json.dump(self.full_db, f, indent=4, default=str)

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

# --- GESTÃO DE TAREFAS (MONDAY STYLE) ---
    def create_task(self, title, description="", priority="Média", assignee=None, due_date=None):
        task_id = str(uuid.uuid4())
        if "tasks" not in self.user_data:
            self.user_data["tasks"] = {}
            
        self.user_data["tasks"][task_id] = {
            "title": title,
            "description": description,
            "status": "To Do",
            "priority": priority,
            "assignee": assignee, # <--- NOVO CAMPO
            "created_at": datetime.now().isoformat(),
            "due_date": str(due_date) if due_date else None
        }
        self.save_db()
        return task_id

    def move_task(self, task_id, new_status):
        if "tasks" in self.user_data and task_id in self.user_data["tasks"]:
            self.user_data["tasks"][task_id]["status"] = new_status
            self.save_db()

    def delete_task(self, task_id):
        if "tasks" in self.user_data and task_id in self.user_data["tasks"]:
            del self.user_data["tasks"][task_id]
            self.save_db()

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
        
        # 1. Limpar figuras anteriores
        plt.clf()
        plt.close('all') 
        
        # 2. FILTRO DE SEGURANÇA (BLACKLIST)
        # Continua a bloquear comandos perigosos de sistema
        dangerous = ["os.", "sys.", "subprocess", "open(", "delete", "rm -rf", "import os", "import sys", "__import__"]
        for word in dangerous:
            if word in code:
                return "⚠️ BLOQUEADO: Tentativa de código não seguro detetada.", None

        # 3. Captura de Output
        old_stdout = sys.stdout
        redirected_output = sys.stdout = StringIO()
        
        # 4. AMBIENTE SEGURO (CORRIGIDO)
        safe_locals = {
            'df': df,
            'pd': pd,
            'plt': plt,
            'sns': sns,
            'np': np,
            're': re
        }
        
        # A MUDANÇA ESTÁ AQUI:
        # Trocámos {"__builtins__": {}} por {} no segundo argumento.
        # Isto permite que a IA use funções básicas como print(), len(), str(), mas 
        # a Blacklist acima continua a impedir que ela importe vírus.
        exec(code, {}, safe_locals)
        
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

def render_dashboard(db, user, persona, api_key, selected_ws_id):
    """
    ANTIGO CÉREBRO: Contém toda a lógica de Chat, Upload e Gráficos.
    """
    # 1. Inicialização de Variáveis de Sessão
    if 'temp_df' not in st.session_state: st.session_state['temp_df'] = None
    if 'temp_files' not in st.session_state: st.session_state['temp_files'] = []
    
    current_id = st.session_state.get('current_chat_id')

    # CENÁRIO 1: CONFIGURAÇÃO DE NOVA ANÁLISE
    if current_id is None:
        st.title(f"📊 Análise IA ({persona})")
        context = st.text_area("Contexto do Negócio", height=70, placeholder="Ex: Varejo de Moda...")
        
        # Upload
        t1, t2 = st.tabs(["📂 Upload Ficheiros", "🔗 Link Cloud"])
        up_files = t1.file_uploader("Ficheiros", accept_multiple_files=True)
        url_input = t2.text_input("Link do Google Sheets (Público)")
        
        # Lógica de Upload
        url_df, url_name = None, None
        if url_input: url_df, url_name = load_from_url(url_input)

        # 1. INICIALIZAÇÃO DE SEGURANÇA
        df = None
        fn = []

        # 2. Processamento Automático
        if up_files or url_df is not None:
            result = smart_merge(up_files, url_df, url_name)
            
            if result is None:
                st.error("Erro ao processar ficheiros.")
            else:
                df, fn = result
                if df is not None and isinstance(df, pd.DataFrame):
                    st.success(f"✅ {len(fn)} Fontes de Dados Conectadas!")
                    st.session_state['temp_df'] = df
                    st.session_state['temp_files'] = fn
                    
                    # BOTÃO RELATÓRIO AUTOMÁTICO
                    if st.button(f"🚀 Relatório Automático ({persona})", use_container_width=True):
                        if not api_key: st.error("Falta API Key")
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

                    with st.expander("Visualizar Dados"): st.dataframe(df.head())
                else:
                    st.warning("Ficheiro carregado mas sem dados legíveis.")

        # Caixa de Pergunta Manual
        if st.session_state.get('temp_df') is not None:
            if query := st.chat_input("O que gostaria de saber?"):
                if not api_key: st.error("Falta API Key")
                else:
                    new_id = db.create_chat(query, workspace_id=selected_ws_id)
                    with st.spinner(f"O {persona} está a analisar..."):
                        code = ask_gemini(st.session_state['temp_df'], query, api_key, context, st.session_state['temp_files'], persona)
                        text, fig = execute_code(code, st.session_state['temp_df'])
                        c_data = db.get_chat(new_id)
                        c_data["messages"].extend([{"role": "user", "content": query}, {"role": "assistant", "content": text}])
                        db.update_chat(new_id, c_data)
                        st.session_state['current_chat_id'] = new_id
                        st.rerun()

    # CENÁRIO 2: DENTRO DE UMA ANÁLISE (CHAT ABERTO)
    else:
        chat_data = db.get_chat(current_id)
        if not chat_data:
            st.error("Erro ao carregar chat.")
            st.session_state['current_chat_id'] = None
            st.rerun()
        
        # Cabeçalho
        c1, c2 = st.columns([3, 1])
        c1.subheader(f"📂 {chat_data['title']}")
        with c2.popover("📤 Partilhar"):
            em = st.text_input("Email")
            if st.button("Convidar"): 
                if db.share_chat(current_id, em): st.success("Partilhado!")

        # Chat vs Notas
        col_chat, col_notes = st.columns([2, 1])
        with col_notes:
            notes = st.text_area("📝 Notas", value=chat_data.get("notes", ""), height=400)
            if notes != chat_data.get("notes", ""):
                chat_data["notes"] = notes; db.update_chat(current_id, chat_data)
        
        with col_chat:
            for msg in chat_data.get("messages", []): st.chat_message(msg["role"]).write(msg["content"])
            
            if query := st.chat_input("Continuar..."):
                if st.session_state.get('temp_df') is None: st.warning("Recarregue os dados.")
                else:
                    st.chat_message("user").write(query)
                    chat_data["messages"].append({"role": "user", "content": query})
                    with st.spinner("Pensando..."):
                        code = ask_gemini(st.session_state['temp_df'], query, api_key, "", st.session_state['temp_files'], persona)
                        text, fig = execute_code(code, st.session_state['temp_df'])
                        st.chat_message("assistant").write(text)
                        if fig: st.chat_message("assistant").pyplot(fig)
                        chat_data["messages"].append({"role": "assistant", "content": text})
                        db.update_chat(current_id, chat_data)
            
            if chat_data.get("messages"):
                st.markdown("---")
                st.download_button("Baixar PDF", create_pdf(chat_data), "report.pdf")


# --- PLACEHOLDERS PARA AS NOVAS FEATURES ---
# --- GESTÃO DE TAREFAS (MONDAY STYLE) ---
    def create_task(self, title, description="", priority="Média", due_date=None):
        task_id = str(uuid.uuid4())
        # Garante que a gaveta 'tasks' existe antes de escrever
        if "tasks" not in self.user_data:
            self.user_data["tasks"] = {}
            
        self.user_data["tasks"][task_id] = {
            "title": title,
            "description": description,
            "status": "To Do",  # Estados: To Do, Doing, Done
            "priority": priority,
            "created_at": datetime.now().isoformat(),
            "due_date": str(due_date) if due_date else None
        }
        self.save_db()
        return task_id

    def move_task(self, task_id, new_status):
        if "tasks" in self.user_data and task_id in self.user_data["tasks"]:
            self.user_data["tasks"][task_id]["status"] = new_status
            self.save_db()

    def delete_task(self, task_id):
        if "tasks" in self.user_data and task_id in self.user_data["tasks"]:
            del self.user_data["tasks"][task_id]
            self.save_db()

def render_docs_page(db):
    st.title("🧠 Documentação")
    st.info("🚧 Módulo em construção: Aqui ficarão os Wikis e Relatórios (Notion Style).")

def render_data_hub_page(db):
    st.title("🧬 Data Hub")
    st.info("🚧 Módulo em construção: Aqui ficará o versionamento de dados (GitHub Style).")


# --- O NOVO CONTROLADOR PRINCIPAL ---

def render_tasks_page(db):
    st.title("🔨 Gestão de Tarefas")
    
    # 1. Formulário de Nova Tarefa
    with st.expander("➕ Nova Tarefa", expanded=False):
        with st.form("new_task_form"):
            c1, c2, c3 = st.columns([2, 1, 1])
            title = c1.text_input("Título da Tarefa")
            prio = c2.selectbox("Prioridade", ["Alta", "Média", "Baixa"])
            assignee = c3.text_input("Atribuir a (Email)") # Campo novo
            desc = st.text_area("Descrição Detalhada")
            
            if st.form_submit_button("Criar Tarefa"):
                if title:
                    # Cria a tarefa
                    db.create_task(title, desc, prio, assignee)
                    
                    # Lógica de Convite Automático
                    if assignee and assignee not in db.full_db["users"]:
                        # Se o user não existe, cria um token
                        token = db.create_one_time_token()
                        app_url = st.secrets.get("APP_URL", "http://localhost:8501")
                        invite_link = f"{app_url}?token={token}"
                        
                        st.success("Tarefa criada!")
                        st.info(f"👤 O utilizador '{assignee}' não está no sistema.")
                        st.code(f"Envia-lhe este link de convite: {invite_link}", language="text")
                        # Opcional: st.stop() para forçar o user a copiar o link antes de recarregar
                    else:
                        st.success("Tarefa Criada e Atribuída!")
                        st.rerun()
                else:
                    st.warning("Escreve um título.")

    st.markdown("---")

    # 2. Preparar Dados
    if "tasks" not in db.user_data: db.user_data["tasks"] = {}
    tasks = db.user_data["tasks"]
    
    todo = {k:v for k,v in tasks.items() if v['status'] == 'To Do'}
    doing = {k:v for k,v in tasks.items() if v['status'] == 'Doing'}
    done = {k:v for k,v in tasks.items() if v['status'] == 'Done'}

    col1, col2, col3 = st.columns(3)

    # Função auxiliar para desenhar o cartão
    def draw_task_card(tid, t, col_type):
        # O Titulo é agora um Expander para ver detalhes
        emoji_prio = "🔥" if t['priority'] == 'Alta' else "▪️"
        
        # Gera um sufixo único baseado no tipo de coluna para evitar conflitos de keys
        # Ex: Se a tarefa estiver no "todo", a key será "start_ID_todo"
        unique_suffix = f"{tid}_{col_type}"

        with st.expander(f"{emoji_prio} {t['title']}", expanded=True):
            # Conteúdo Oculto (Descrição e Dono)
            if t.get('description'):
                st.markdown(f"**📝 Descrição:**\n{t['description']}")
            else:
                st.caption("Sem descrição.")
            
            st.markdown("---")
            assigned_to = t.get('assignee') if t.get('assignee') else "Ninguém"
            st.caption(f"👤 **Responsável:** {assigned_to}")
            
            # Botões de Ação
            c_btns = st.columns(2)
            
            if col_type == "todo":
                # Adicionei o sufixo _todo à key
                if st.button("➡️ Iniciar", key=f"start_{unique_suffix}", use_container_width=True):
                    db.move_task(tid, "Doing"); st.rerun()
            
            elif col_type == "doing":
                # Adicionei sufixos _back e _done
                if c_btns[0].button("⬅️", key=f"back_{unique_suffix}", use_container_width=True):
                    db.move_task(tid, "To Do"); st.rerun()
                if c_btns[1].button("✅", key=f"done_{unique_suffix}", use_container_width=True):
                    db.move_task(tid, "Done"); st.rerun()
            
            elif col_type == "done":
                # Adicionei sufixo _reopen
                if st.button("♻️ Reabrir", key=f"reopen_{unique_suffix}", use_container_width=True):
                    db.move_task(tid, "To Do"); st.rerun()
            
            # Botão de apagar (sempre presente)
            if st.button("🗑️ Apagar", key=f"del_{unique_suffix}", use_container_width=True):
                db.delete_task(tid); st.rerun()

    # 3. Desenhar Colunas
    with col1:
        st.subheader("📌 A Fazer")
        for tid, t in todo.items(): draw_task_card(tid, t, "todo")

    with col2:
        st.subheader("⚙️ Em Progresso")
        for tid, t in doing.items(): draw_task_card(tid, t, "doing")

    with col3:
        st.subheader("🎉 Concluído")
        for tid, t in done.items(): draw_task_card(tid, t, "done")

    # 2. Lógica do Kanban
    # Garante que a gaveta de tarefas existe
    if "tasks" not in db.user_data:
        db.user_data["tasks"] = {}
        
    tasks = db.user_data["tasks"]
    todo = {k:v for k,v in tasks.items() if v['status'] == 'To Do'}
    doing = {k:v for k,v in tasks.items() if v['status'] == 'Doing'}
    done = {k:v for k,v in tasks.items() if v['status'] == 'Done'}

    # 3. Desenhar as 3 Colunas
    col1, col2, col3 = st.columns(3)

    # --- COLUNA TO DO ---
    with col1:
        st.subheader("📌 A Fazer")
        for tid, t in todo.items():
            with st.container(border=True):
                st.markdown(f"**{t['title']}**")
                if t['priority'] == 'Alta': st.caption("🔥 Alta Prioridade")
                
                # Botão para mover para a direita (Doing)
                if st.button("➡️ Iniciar", key=f"start_{tid}"):
                    db.move_task(tid, "Doing")
                    st.rerun()
                
                if st.button("🗑️", key=f"del_{tid}"):
                    db.delete_task(tid)
                    st.rerun()

    # --- COLUNA DOING ---
    with col2:
        st.subheader("⚙️ Em Progresso")
        for tid, t in doing.items():
            with st.container(border=True):
                st.markdown(f"**{t['title']}**")
                st.caption(f"Desde: {t['created_at'][:10]}")
                
                c_a, c_b = st.columns(2)
                if c_a.button("⬅️", key=f"back_{tid}"): # Voltar para To Do
                    db.move_task(tid, "To Do")
                    st.rerun()
                if c_b.button("✅", key=f"finish_{tid}"): # Ir para Done
                    db.move_task(tid, "Done")
                    st.rerun()

    # --- COLUNA DONE ---
    with col3:
        st.subheader("🎉 Concluído")
        for tid, t in done.items():
            with st.container(border=True):
                st.markdown(f"~~{t['title']}~~") # Riscado
                st.caption("Concluído")
                
                if st.button("♻️ Reabrir", key=f"reopen_{tid}"):
                    db.move_task(tid, "To Do")
                    st.rerun()




def main_app():
    user = st.session_state.get('username', 'User')
    is_guest = st.session_state.get('is_guest', False)
    db = HistoryManager(user)

    # 1. SIDEBAR DE NAVEGAÇÃO (AInsight OS)
    with st.sidebar:
        st.header("👁️ AInsight OS")
        st.caption(f"User: {user}")
        
        # O MENU PRINCIPAL
        page = st.radio("Navegação", ["📊 Análise IA", "🧬 Data Hub", "🔨 Tarefas", "🧠 Docs"])
        
        st.markdown("---")
        
        # CONFIGURAÇÕES ESPECÍFICAS DA ANÁLISE
        # Só mostramos Personas e Workspaces se estivermos na aba de Análise
        selected_ws_id = None
        persona = "Data Scientist" # Default
        
        if page == "📊 Análise IA":
            context_mode = st.radio("Contexto:", ["Pessoal", "Workspaces"], horizontal=True)
            if context_mode == "Workspaces":
                if db.user_data["plan"] != "pro":
                    if st.button("💎 Upgrade Pro"): db.upgrade_plan(); st.rerun()
                else:
                    my_ws = {k:v for k,v in db.full_db["workspaces"].items() if user in v["members"]}
                    if my_ws: selected_ws_id = st.selectbox("Workspace", list(my_ws.keys()), format_func=lambda x: my_ws[x]["name"])
                    with st.popover("Novo Workspace"):
                        n = st.text_input("Nome")
                        if st.button("Criar"): db.create_workspace(n); st.rerun()
            
            st.markdown("---")
            persona = st.selectbox("Persona", ["Data Scientist", "CFO (Financeiro)", "CMO (Marketing)", "COO (Operacional)"])
            
            if st.button("➕ Nova Análise", use_container_width=True):
                st.session_state['current_chat_id'] = None; st.rerun()
            
            # Histórico Rápido na Sidebar
            chats_source = db.user_chats if not selected_ws_id else db.full_db["workspaces"][selected_ws_id]["chats"]
            with st.expander("Histórico Recente"):
                for cid, d in sorted(chats_source.items(), key=lambda x:x[1]['created_at'], reverse=True)[:5]:
                    if st.button(f"💬 {d['title'][:20]}...", key=cid): st.session_state['current_chat_id'] = cid; st.rerun()

        st.markdown("---")
        if st.button("🚪 Sair"): 
            st.session_state['authenticated'] = False
            st.rerun()

    # 2. ROUTER - ESCOLHE O QUE MOSTRAR NO ECRÃ
    api_key = st.secrets.get("GEMINI_API_KEY") or st.text_input("API Key (se não configurada)", type="password")
    
    if page == "📊 Análise IA":
        render_dashboard(db, user, persona, api_key, selected_ws_id)
    elif page == "🧬 Data Hub":
        render_data_hub_page(db)
    elif page == "🔨 Tarefas":
        render_tasks_page(db)
    elif page == "🧠 Docs":
        render_docs_page(db)
if __name__ == "__main__":
    if "authenticated" not in st.session_state: 
        st.session_state["authenticated"] = False
    
    if st.session_state["authenticated"]: 
        main_app()
    else: 
        login_page()