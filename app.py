import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import google.generativeai as genai
import sys
from io import StringIO
import re
from functools import reduce
from fpdf import FPDF
import time

# --- CONFIGURAÇÃO GLOBAL ---
st.set_page_config(page_title="Data Intelligence Hub", page_icon="🔒", layout="wide")

# --- FUNÇÕES DO SISTEMA (AS MESMAS DE ANTES) ---
def clean_individual_df(df, filename):
    df.drop_duplicates(inplace=True)
    date_col = None
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]): date_col = col; break
    if not date_col:
        for col in df.columns:
            if df[col].dtype == 'object':
                try: df[col] = pd.to_datetime(df[col]); date_col = col; break
                except: pass
    if date_col:
        df.rename(columns={date_col: 'DATA_FUSAO'}, inplace=True)
        return df, True
    return df, False

def smart_merge(files):
    dataframes = []
    file_names = []
    for file in files:
        try:
            if file.name.endswith('.csv'): df = pd.read_csv(file)
            else: df = pd.read_excel(file)
            clean_df, tem_data = clean_individual_df(df, file.name)
            if tem_data:
                prefix = file.name.split('.')[0]
                cols = {c: f"{prefix}_{c}" for c in clean_df.columns if c != 'DATA_FUSAO'}
                clean_df.rename(columns=cols, inplace=True)
                dataframes.append(clean_df)
                file_names.append(file.name)
        except: pass
    
    if not dataframes: return None, "Erro na leitura."
    if len(dataframes) == 1: return dataframes[0], file_names
    try:
        df_final = reduce(lambda left, right: pd.merge(left, right, on='DATA_FUSAO', how='outer'), dataframes)
        return df_final.sort_values('DATA_FUSAO').fillna(0), file_names
    except Exception as e: return None, str(e)

def ask_gemini(df, query, api_key, context, file_list, persona):
    genai.configure(api_key=api_key)
    persona_prompt = "Atue como Data Scientist."
    if persona == "CFO": persona_prompt = "Atue como Diretor Financeiro focado em ROI."
    elif persona == "CMO": persona_prompt = "Atue como Diretor de Marketing focado em crescimento."

    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = f"""
    {persona_prompt}
    CONTEXTO: {context}
    DADOS: {", ".join(file_list)}
    ESTRUTURA: {df.dtypes.to_string()}
    PERGUNTA: "{query}"
    REGRAS: Responda APENAS com código Python (```python). Use 'df', print() e plt.figure().
    """
    response = model.generate_content(prompt)
    match = re.search(r"```python(.*?)```", response.text, re.DOTALL)
    return match.group(1).strip() if match else response.text.replace("```", "").strip()

def execute_code(code, df):
    try:
        import numpy as np
        old_stdout = sys.stdout
        redirected_output = sys.stdout = StringIO()
        local_vars = {'df': df, 'plt': plt, 'sns': sns, 'pd': pd, 'np': np}
        exec(code, {}, local_vars)
        sys.stdout = old_stdout
        return redirected_output.getvalue(), plt
    except Exception as e: return f"Erro: {e}", None

def create_pdf(chat_history):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt="Relatorio AI Data Hub", ln=1, align='C')
    pdf.ln(10)
    for msg in chat_history:
        role = "IA" if msg["role"] == "assistant" else "USER"
        text = msg["content"].replace("€", "EUR").encode('latin-1', 'replace').decode('latin-1')
        pdf.set_font("Arial", 'B', 10); pdf.cell(0, 10, txt=f"[{role}]", ln=1)
        pdf.set_font("Arial", size=10); pdf.multi_cell(0, 10, txt=text); pdf.ln(5)
    return pdf.output(dest='S').encode('latin-1')

# --- ECRÃ 1: PÁGINA DE LOGIN (WELCOME PAGE) ---
def login_page():
    st.markdown("""
        <style>
        .login-container {
            padding: 50px;
            border-radius: 10px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            text-align: center;
        }
        </style>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        st.markdown("## 🔐 Acesso Restrito")
        st.markdown("### AI Data Intelligence Hub")
        st.markdown("---")
        
        username = st.text_input("Utilizador")
        password = st.text_input("Password", type="password")
        
        if st.button("Entrar no Sistema", use_container_width=True):
            # Verificar credenciais (simples ou via Secrets)
            real_user = st.secrets.get("ADMIN_USER", "admin")
            real_pass = st.secrets.get("ADMIN_PASSWORD", "123")
            
            if username == real_user and password == real_pass:
                st.success("Login com sucesso! A carregar...")
                time.sleep(1) # Efeito visual
                st.session_state['authenticated'] = True
                st.rerun() # Recarrega a página para entrar no sistema
            else:
                st.error("Credenciais incorretas.")
        
        st.markdown("---")
        st.caption("© 2024 Enterprise Data Solutions")

# --- ECRÃ 2: APLICAÇÃO PRINCIPAL (SÓ APARECE DEPOIS DO LOGIN) ---
def main_app():
    # Botão de Logout na Sidebar
    with st.sidebar:
        st.title("🏢 Enterprise AI")
        if st.button("🚪 Sair / Logout"):
            st.session_state['authenticated'] = False
            st.rerun()
        
        st.markdown("---")
        # API Key Automática
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.success("🔑 Sistema Ativo")
        else:
            api_key = st.text_input("API Key", type="password")

        st.markdown("---")
        persona = st.selectbox("Persona", ["Data Scientist", "CFO", "CMO"])
        context = st.text_area("Contexto", height=80)

    # Área Principal
    st.title(f"Olá, {st.secrets.get('ADMIN_USER', 'Admin')} 👋")
    st.markdown("Carregue os dados para iniciar a análise estratégica.")

    if "messages" not in st.session_state: st.session_state.messages = []
    
    # Botão Limpar
    if st.button("🗑️ Limpar Análise Atual"):
        st.session_state.messages = []
        st.rerun()

    uploaded_files = st.file_uploader("", accept_multiple_files=True)

    if uploaded_files and api_key:
        df_final, file_names = smart_merge(uploaded_files)
        if df_final is not None:
            with st.expander("📂 Dados Carregados", expanded=True):
                st.dataframe(df_final.head())

            # Histórico
            for msg in st.session_state.messages:
                st.chat_message(msg["role"]).write(msg["content"])
                if "image" in msg: st.chat_message(msg["role"]).pyplot(msg["image"])

            query = st.chat_input("Faça a sua pergunta...")
            if query:
                st.session_state.messages.append({"role": "user", "content": query})
                st.chat_message("user").write(query)
                with st.spinner("A analisar..."):
                    code = ask_gemini(df_final, query, api_key, context, file_names, persona)
                    text, fig = execute_code(code, df_final)
                    
                    entry = {"role": "assistant", "content": text}
                    st.chat_message("assistant").write(text)
                    if fig and fig.get_fignums():
                        st.chat_message("assistant").pyplot(fig)
                        entry["image"] = fig
                    st.session_state.messages.append(entry)
            
            if st.session_state.messages:
                pdf_bytes = create_pdf(st.session_state.messages)
                st.download_button("📄 Download PDF", pdf_bytes, "relatorio.pdf", "application/pdf")

# --- CONTROLADOR DE FLUXO ---
if __name__ == "__main__":
    # Se não tiver estado definido, define como falso
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    # Se estiver autenticado, mostra a App. Se não, mostra Login.
    if st.session_state["authenticated"]:
        main_app()
    else:
        login_page()