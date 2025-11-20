import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import google.generativeai as genai
import sys
from io import StringIO
from sklearn.linear_model import LinearRegression
import numpy as np
from functools import reduce

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="AI Data Hub - Multi Source", page_icon="🔗", layout="wide")

# --- 1. FUNÇÃO DE LIMPEZA INDIVIDUAL (CORRIGIDA) ---
def clean_individual_df(df):
    # Remover duplicados
    df.drop_duplicates(inplace=True)
    
    date_col = None

    # PASSO 1: Verificar se alguma coluna JÁ É data (formato datetime)
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            date_col = col
            break # Encontrámos!

    # PASSO 2: Se não encontrou, tentar converter colunas de TEXTO
    if not date_col:
        for col in df.columns:
            if df[col].dtype == 'object':
                try:
                    # Tenta converter. Se falhar, vai para o 'except'
                    df[col] = pd.to_datetime(df[col])
                    date_col = col
                    break
                except:
                    pass
    
    # Preencher vazios (Tratamento de erros)
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].fillna("Desconhecido")
        else:
            df[col] = df[col].fillna(0)
            
    return df, date_col

# --- 2. MOTOR DE FUSÃO ---
def smart_merge(files):
    dataframes = []
    file_names = []
    
    for file in files:
        try:
            if file.name.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
            
            clean_df, date_col = clean_individual_df(df)
            
            if date_col:
                clean_df = clean_df.sort_values(date_col)
                prefix = file.name.split('.')[0]
                # Renomear colunas para evitar conflitos, exceto a Data
                clean_df.columns = [f"{prefix}_{c}" if c != date_col else date_col for c in clean_df.columns]
                dataframes.append(clean_df)
                file_names.append(file.name)
            else:
                st.warning(f"⚠️ Ignorado: {file.name} (Não encontrei coluna de Data)")
        except Exception as e:
            st.error(f"Erro ao ler {file.name}: {e}")

    if not dataframes:
        return None, "Nenhum ficheiro válido para fusão."

    # Fundir (Outer Join pela Data)
    try:
        df_final = reduce(lambda left, right: pd.merge_ordered(left, right, on=left.columns[0], how='outer', fill_method=None), dataframes)
        df_final = df_final.fillna(0)
        return df_final, file_names
    except Exception as e:
        return None, f"Erro na fusão: {e}"

# --- 3. CÉREBRO GEMINI ---
def ask_gemini(df, query, api_key, context, file_list):
    genai.configure(api_key=api_key)
    
    # Auto-detect model
    chosen_model = "gemini-1.5-flash"
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'flash' in m.name: chosen_model = m.name; break
                elif 'pro' in m.name: chosen_model = m.name
    except: pass 

    model = genai.GenerativeModel(chosen_model)

    columns_info = df.dtypes.to_string()
    head_info = df.head(3).to_string()
    files_str = ", ".join(file_list)

    prompt = f"""
    Você é um Analista de Dados Senior especializado em correlação de múltiplas fontes.
    
    CONTEXTO: {context}
    FONTES: {files_str}
    ESTRUTURA (df): {columns_info}
    AMOSTRA: {head_info}
    PERGUNTA: "{query}"
    
    OBJETIVO:
    Analise correlações entre ficheiros diferentes.
    
    REGRAS:
    1. Use 'df' diretamente.
    2. Use print() para explicar insights.
    3. Use plt.figure() para gráficos.
    4. APENAS CÓDIGO PYTHON.
    """
    
    response = model.generate_content(prompt)
    return response.text.replace("```python", "").replace("```", "").strip()

# --- 4. EXECUTOR ---
def execute_code(code, df):
    try:
        old_stdout = sys.stdout
        redirected_output = sys.stdout = StringIO()
        local_vars = {'df': df, 'plt': plt, 'sns': sns, 'pd': pd, 'np': np}
        exec(code, {}, local_vars)
        sys.stdout = old_stdout
        return redirected_output.getvalue(), plt
    except Exception as e:
        return f"Erro: {e}", None

# --- 5. INTERFACE ---
def main():
    st.title("🔗 AI Data Hub: Análise Multi-Ficheiro")
    
    with st.sidebar:
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.success("Chave ativa!")
        else:
            api_key = st.text_input("API Key", type="password")
            
        st.markdown("---")
        business_context = st.text_area("Contexto do Negócio", height=100, placeholder="Ex: Vendas vs Marketing...")

    # UPLOAD
    uploaded_files = st.file_uploader("Carregue Marketing.xlsx e Vendas.xlsx juntos", accept_multiple_files=True)

    if uploaded_files and api_key:
        with st.spinner("A fundir ficheiros..."):
            df_final, file_names = smart_merge(uploaded_files)
        
        if df_final is not None:
            st.success(f"✅ Fusão concluída: {', '.join(file_names)}")
            
            with st.expander("Ver Super Tabela"):
                st.dataframe(df_final.head())

            query = st.chat_input("Ex: Qual a correlação entre Google Ads e Vendas Totais?")
            if query:
                st.chat_message("user").write(query)
                with st.spinner("A analisar..."):
                    code = ask_gemini(df_final, query, api_key, business_context, file_names)
                    text, fig = execute_code(code, df_final)
                    
                    if text: st.chat_message("assistant").write(text)
                    if fig and fig.get_fignums(): st.chat_message("assistant").pyplot(fig); fig.clf()
        elif file_names and isinstance(file_names, str):
             st.error(file_names)

if __name__ == "__main__":
    main()