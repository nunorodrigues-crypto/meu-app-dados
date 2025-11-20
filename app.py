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

# --- 1. FUNÇÃO DE LIMPEZA INDIVIDUAL ---
def clean_individual_df(df):
    # Remover duplicados
    df.drop_duplicates(inplace=True)
    
    # Detetar e converter Datas
    date_col = None
    for col in df.columns:
        if df[col].dtype == 'object':
            try:
                df[col] = pd.to_datetime(df[col])
                date_col = col # Guardamos qual é a coluna de data
            except:
                pass
    
    # Preencher vazios
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].fillna("Desconhecido")
        else:
            df[col] = df[col].fillna(0) # Assumimos 0 para números (melhor para somas)
            
    return df, date_col

# --- 2. MOTOR DE FUSÃO (O SEGREDO DA INTERATIVIDADE) ---
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
            
            # Se tiver data, vamos usá-la como índice para fundir
            if date_col:
                clean_df = clean_df.sort_values(date_col)
                # Renomear colunas para incluir o nome do ficheiro (para não misturar "Valor" com "Valor")
                prefix = file.name.split('.')[0]
                clean_df.columns = [f"{prefix}_{c}" if c != date_col else date_col for c in clean_df.columns]
                dataframes.append(clean_df)
                file_names.append(file.name)
            else:
                st.warning(f"⚠️ O ficheiro {file.name} não tem coluna de Data detetável. Será ignorado na fusão.")
        except Exception as e:
            st.error(f"Erro ao ler {file.name}: {e}")

    if not dataframes:
        return None, "Nenhum ficheiro válido para fusão."

    # Fundir todos os dataframes pela coluna de Data (Outer Join)
    # Isto cria a "Super Tabela"
    try:
        df_final = reduce(lambda left, right: pd.merge_ordered(left, right, on=left.columns[0], how='outer', fill_method=None), dataframes)
        # Preencher buracos gerados pela fusão com 0
        df_final = df_final.fillna(0)
        return df_final, file_names
    except Exception as e:
        return None, f"Erro na fusão: {e}"

# --- 3. CÉREBRO GEMINI MULTI-SOURCE ---
def ask_gemini(df, query, api_key, context, file_list):
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash") # Ou Pro

    columns_info = df.dtypes.to_string()
    head_info = df.head(3).to_string()
    files_str = ", ".join(file_list)

    prompt = f"""
    Você é um Analista de Dados Senior especializado em correlação de múltiplas fontes.
    
    CONTEXTO DO CLIENTE:
    {context}
    
    FONTES DE DADOS (Ficheiros fundidos numa única Super Tabela):
    Ficheiros originais: {files_str}
    
    ESTRUTURA DA SUPER TABELA (df):
    {columns_info}
    
    AMOSTRA:
    {head_info}
    
    PERGUNTA: "{query}"
    
    OBJETIVO:
    1. Analisar correlações entre colunas que vieram de ficheiros diferentes (ex: Vendas vs Marketing).
    2. Gerar código Python para responder.
    
    REGRAS:
    1. Use 'df' diretamente.
    2. Use print() para explicar insights.
    3. Use plt.figure() para gráficos.
    4. APENAS CÓDIGO.
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

# --- 5. INTERFACE PRINCIPAL ---
def main():
    st.title("🔗 AI Data Hub: Análise Multi-Ficheiro")
    
    with st.sidebar:
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.success("Chave ativa!")
        else:
            api_key = st.text_input("API Key", type="password")
            
        st.markdown("---")
        business_context = st.text_area("Contexto do Negócio", height=100)

    # UPLOAD MÚLTIPLO
    uploaded_files = st.file_uploader("Carregue VÁRIOS ficheiros (Excel/CSV) para cruzar dados", accept_multiple_files=True)

    if uploaded_files and api_key:
        # Processar e Fundir
        with st.spinner("A fundir ficheiros e criar conexões..."):
            df_final, file_names = smart_merge(uploaded_files)
        
        if df_final is not None:
            st.success(f"✅ Sucesso! {len(file_names)} ficheiros fundidos numa Super Tabela.")
            
            with st.expander("Ver Super Tabela (Dados Cruzados)"):
                st.dataframe(df_final.head())

            # CHAT
            query = st.chat_input("Ex: Como é que o Investimento (Ficheiro A) impactou as Vendas (Ficheiro B)?")
            if query:
                st.chat_message("user").write(query)
                with st.spinner("A analisar correlações entre ficheiros..."):
                    code = ask_gemini(df_final, query, api_key, business_context, file_names)
                    text, fig = execute_code(code, df_final)
                    
                    if text: st.chat_message("assistant").write(text)
                    if fig and fig.get_fignums(): st.chat_message("assistant").pyplot(fig); fig.clf()
        else:
            st.error(file_names) # Mostra o erro se a fusão falhar

if __name__ == "__main__":
    main()