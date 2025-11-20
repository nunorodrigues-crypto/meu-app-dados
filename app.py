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

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="AI Data Hub - Debug Mode", page_icon="🛠️", layout="wide")

# --- 1. LIMPEZA COM DIAGNÓSTICO ---
def clean_individual_df(df, filename):
    # Mostrar colunas encontradas (Debug)
    cols_encontradas = list(df.columns)
    
    df.drop_duplicates(inplace=True)
    date_col = None

    # Tenta achar data
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
                except:
                    pass
    
    # Feedback Visual para o Utilizador
    if date_col:
        st.toast(f"✅ {filename}: Data detetada em '{date_col}'")
    else:
        st.error(f"❌ {filename}: NÃO encontrei data. Colunas disponíveis: {cols_encontradas}")
    
    # Preencher vazios
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].fillna("Desconhecido")
        else:
            df[col] = df[col].fillna(0)
            
    return df, date_col

# --- 2. FUSÃO ---
def smart_merge(files):
    dataframes = []
    file_names = []
    
    for file in files:
        try:
            if file.name.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
            
            clean_df, date_col = clean_individual_df(df, file.name)
            
            if date_col:
                clean_df = clean_df.sort_values(date_col)
                prefix = file.name.split('.')[0]
                clean_df.columns = [f"{prefix}_{c}" if c != date_col else date_col for c in clean_df.columns]
                dataframes.append(clean_df)
                file_names.append(file.name)
        except Exception as e:
            st.error(f"Erro crítico ao ler {file.name}: {e}")

    if not dataframes:
        return None, "Nenhum ficheiro válido processado."

    # Tenta fundir
    try:
        if len(dataframes) == 1:
            return dataframes[0], file_names
            
        df_final = reduce(lambda left, right: pd.merge_ordered(left, right, on=left.columns[0], how='outer', fill_method=None), dataframes)
        df_final = df_final.fillna(0)
        return df_final, file_names
    except Exception as e:
        return None, f"Erro na fusão matemática: {e}"

# --- 3. CÉREBRO ---
def ask_gemini(df, query, api_key, context, file_list):
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
    
    prompt = f"""
    Você é um Analista de Dados Expert.
    CONTEXTO: {context}
    FICHEIROS ORIGINAIS: {", ".join(file_list)}
    
    ESTRUTURA DOS DADOS (df):
    {df.dtypes.to_string()}
    
    AMOSTRA:
    {df.head(3).to_string()}
    
    PERGUNTA: "{query}"
    
    OBJETIVO: Analisar correlação e causalidade.
    REGRAS:
    1. Use 'df'.
    2. Use print() para texto.
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
        return f"Erro de Código: {e}", None

# --- 5. INTERFACE ---
def main():
    st.title("🛠️ AI Data Hub (Modo Diagnóstico)")
    
    with st.sidebar:
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.success("Chave OK")
        else:
            api_key = st.text_input("API Key", type="password")
        st.markdown("---")
        business_context = st.text_area("Contexto", height=100)

    uploaded_files = st.file_uploader("Carregue os ficheiros", accept_multiple_files=True)

    if uploaded_files and api_key:
        st.write("---")
        st.write("🔍 **Iniciando Diagnóstico dos Ficheiros...**")
        
        df_final, file_names = smart_merge(uploaded_files)
        
        if df_final is not None:
            st.success(f"✅ Fusão Sucesso! Linhas totais: {len(df_final)}")
            st.dataframe(df_final.head())
            
            query = st.chat_input("Faça a sua pergunta...")
            if query:
                st.chat_message("user").write(query)
                with st.spinner("A pensar..."):
                    code = ask_gemini(df_final, query, api_key, business_context, file_names)
                    text, fig = execute_code(code, df_final)
                    if text: st.chat_message("assistant").write(text)
                    if fig and fig.get_fignums(): st.chat_message("assistant").pyplot(fig); fig.clf()
        else:
            st.error(f"Falha na fusão: {file_names}")

if __name__ == "__main__":
    main()