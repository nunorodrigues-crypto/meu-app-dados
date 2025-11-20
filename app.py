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
import base64

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Enterprise Data AI", page_icon="🏢", layout="wide")

# --- 0. SEGURANÇA (LOGIN SIMPLES) ---
def check_password():
    """Retorna True se o utilizador tiver a password certa."""
    # Se não houver senha definida nos segredos, deixa entrar (modo dev)
    if "APP_PASSWORD" not in st.secrets:
        return True

    def password_entered():
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Apagar senha da memória
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # Primeira vez
        st.text_input("🔒 Insira a Password de Acesso", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        # Senha errada
        st.text_input("🔒 Insira a Password de Acesso", type="password", on_change=password_entered, key="password")
        st.error("😕 Password incorreta.")
        return False
    else:
        # Senha correta
        return True

# --- 1. GERADOR DE PDF ---
def create_pdf(chat_history):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    
    pdf.cell(200, 10, txt="Relatorio de Analise - AI Data Hub", ln=1, align='C')
    pdf.ln(10)
    
    for message in chat_history:
        role = "IA" if message["role"] == "assistant" else "UTILIZADOR"
        text = message["content"]
        # Limpar caracteres que quebram o PDF (ex: emojis ou €)
        text = text.replace("€", "EUR").encode('latin-1', 'replace').decode('latin-1')
        
        pdf.set_font("Arial", 'B', 10)
        pdf.cell(0, 10, txt=f"[{role}]", ln=1)
        pdf.set_font("Arial", size=10)
        pdf.multi_cell(0, 10, txt=text)
        pdf.ln(5)
        
    return pdf.output(dest='S').encode('latin-1')

# --- 2. LIMPEZA E FUSÃO ---
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

# --- 3. CÉREBRO ---
def ask_gemini(df, query, api_key, context, file_list, persona):
    genai.configure(api_key=api_key)
    
    # Ajustar tom de voz baseada na Persona
    persona_prompt = ""
    if persona == "CFO (Financeiro)":
        persona_prompt = "Atue como um Diretor Financeiro Rígido. Foque APENAS em ROI, lucro, margens e eficiência. Seja direto."
    elif persona == "CMO (Marketing)":
        persona_prompt = "Atue como um Diretor de Marketing Criativo. Foque em crescimento, alcance, conversão e oportunidades de mercado."
    else:
        persona_prompt = "Atue como um Cientista de Dados Sênior. Foque em correlações estatísticas, tendências e precisão dos dados."

    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = f"""
    {persona_prompt}
    CONTEXTO: {context}
    DADOS: {", ".join(file_list)}
    ESTRUTURA: {df.dtypes.to_string()}
    PERGUNTA: "{query}"
    
    REGRAS:
    1. Responda com código Python (dentro de ```python).
    2. Use 'df'.
    3. Use print() para texto.
    4. Use plt.figure() para gráficos (sem plt.show()).
    """
    response = model.generate_content(prompt)
    match = re.search(r"```python(.*?)```", response.text, re.DOTALL)
    return match.group(1).strip() if match else response.text.replace("```", "").strip()

# --- 4. EXECUTOR ---
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

# --- 5. INTERFACE PRINCIPAL ---
def main():
    if not check_password():
        return  # Para a execução se não tiver logado

    st.sidebar.title("🏢 Enterprise AI")
    
    # Setup Chave API
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.sidebar.success("🔑 Sistema Autenticado")
    else:
        api_key = st.sidebar.text_input("API Key", type="password")

    # Setup Persona
    st.sidebar.markdown("---")
    persona = st.sidebar.selectbox("Quem vai analisar?", ["Data Scientist (Padrão)", "CFO (Financeiro)", "CMO (Marketing)"])
    business_context = st.sidebar.text_area("Contexto", height=80, placeholder="Ex: E-commerce de sapatos...")

    # Gestão de Sessão (Histórico)
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Botão Reset
    if st.sidebar.button("🗑️ Limpar Conversa"):
        st.session_state.messages = []
        st.rerun()

    # Título
    st.title(f"Analista Virtual: {persona}")

    # Upload
    uploaded_files = st.file_uploader("Carregar Dados", accept_multiple_files=True)

    if uploaded_files and api_key:
        df_final, file_names = smart_merge(uploaded_files)
        
        if df_final is not None:
            with st.expander("📂 Ver Dados Consolidados"):
                st.dataframe(df_final.head())

            # Mostrar Histórico
            for msg in st.session_state.messages:
                st.chat_message(msg["role"]).write(msg["content"])
                if "image" in msg:
                    st.chat_message(msg["role"]).pyplot(msg["image"])

            # Chat Input
            query = st.chat_input("Faça a sua pergunta...")
            if query:
                st.session_state.messages.append({"role": "user", "content": query})
                st.chat_message("user").write(query)
                
                with st.spinner(f"O {persona} está a pensar..."):
                    code = ask_gemini(df_final, query, api_key, business_context, file_names, persona)
                    text, fig = execute_code(code, df_final)
                    
                    response_entry = {"role": "assistant", "content": text}
                    st.chat_message("assistant").write(text)
                    
                    if fig and fig.get_fignums():
                        st.chat_message("assistant").pyplot(fig)
                        response_entry["image"] = fig
                    
                    st.session_state.messages.append(response_entry)

            # --- BOTÃO EXPORTAR (SÓ APARECE SE HOUVER CONVERSA) ---
            if st.session_state.messages:
                st.markdown("---")
                col1, col2 = st.columns([4, 1])
                with col2:
                    pdf_bytes = create_pdf(st.session_state.messages)
                    st.download_button(
                        label="📄 Baixar Relatório PDF",
                        data=pdf_bytes,
                        file_name="relatorio_ai.pdf",
                        mime="application/pdf"
                    )

if __name__ == "__main__":
    main()