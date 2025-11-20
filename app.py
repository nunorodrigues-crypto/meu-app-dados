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
st.set_page_config(page_title="AI Data Hub - Final", page_icon="🔗", layout="wide")

# --- 1. LIMPEZA E PADRONIZAÇÃO ---
def clean_individual_df(df, filename):
    df.drop_duplicates(inplace=True)
    
    # Tenta encontrar a coluna de DATA
    date_col = None
    
    # 1. Procura por tipo datetime
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            date_col = col
            break
            
    # 2. Se não achar, tenta converter texto
    if not date_col:
        for col in df.columns:
            if df[col].dtype == 'object':
                try:
                    pd.to_datetime(df[col]) # Teste
                    df[col] = pd.to_datetime(df[col]) # Conversão
                    date_col = col
                    break
                except:
                    pass

    # 3. SE ACHOU DATA: Renomeia para 'DATA_FUSAO' (O Segredo!)
    if date_col:
        df.rename(columns={date_col: 'DATA_FUSAO'}, inplace=True)
        st.toast(f"✅ {filename}: Data encontrada em '{date_col}' -> Padronizada.")
        return df, True
    else:
        st.error(f"❌ {filename}: Nenhuma coluna de data encontrada.")
        return df, False

# --- 2. FUSÃO INTELIGENTE ---
def smart_merge(files):
    dataframes = []
    file_names = []
    
    for file in files:
        try:
            if file.name.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
            
            clean_df, tem_data = clean_individual_df(df, file.name)
            
            if tem_data:
                # Renomear as OUTRAS colunas com o prefixo do ficheiro
                prefix = file.name.split('.')[0]
                cols_novas = {}
                for c in clean_df.columns:
                    if c != 'DATA_FUSAO':
                        cols_novas[c] = f"{prefix}_{c}"
                
                clean_df.rename(columns=cols_novas, inplace=True)
                dataframes.append(clean_df)
                file_names.append(file.name)
                
        except Exception as e:
            st.error(f"Erro ao ler {file.name}: {e}")

    if not dataframes:
        return None, "Sem ficheiros válidos."

    # FUNDIR TUDO PELA 'DATA_FUSAO'
    try:
        df_final = reduce(lambda left, right: pd.merge(left, right, on='DATA_FUSAO', how='outer'), dataframes)
        
        # Ordenar por data e preencher vazios
        df_final = df_final.sort_values('DATA_FUSAO')
        df_final = df_final.fillna(0)
        return df_final, file_names
    except Exception as e:
        return None, f"Erro técnico na fusão: {e}"

# --- 3. CÉREBRO IA ---
def ask_gemini(df, query, api_key, context, file_list):
    genai.configure(api_key=api_key)
    
    # Tenta encontrar modelo disponível
    chosen_model = "gemini-1.5-flash"
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'flash' in m.name: chosen_model = m.name; break
                elif 'pro' in m.name: chosen_model = m.name
    except: pass 
    
    model = genai.GenerativeModel(chosen_model)
    
    prompt = f"""
    Você é um Analista de Dados.
    CONTEXTO: {context}
    FONTE DE DADOS: {", ".join(file_list)}
    
    TABELA UNIFICADA (df):
    {df.dtypes.to_string()}
    
    AMOSTRA:
    {df.head(3).to_string()}
    
    PERGUNTA: "{query}"
    
    REGRAS:
    1. Use 'df' (a coluna de tempo chama-se 'DATA_FUSAO').
    2. Use print() para explicar.
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
        return f"Erro no código gerado: {e}", None

# --- 5. INTERFACE ---
def main():
    st.title("🔗 AI Data Hub: Fusão Perfeita")
    
    with st.sidebar:
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.success("Chave OK")
        else:
            api_key = st.text_input("API Key", type="password")
        st.markdown("---")
        business_context = st.text_area("Contexto do Negócio", height=100)

    uploaded_files = st.file_uploader("Carregue os ficheiros", accept_multiple_files=True)

    if uploaded_files and api_key:
        st.divider()
        df_final, file_names = smart_merge(uploaded_files)
        
        if df_final is not None:
            st.success(f"✅ {len(file_names)} ficheiros conectados com sucesso!")
            
            with st.expander("Ver Tabela Unificada"):
                st.dataframe(df_final.head())

            query = st.chat_input("Pergunte sobre as correlações...")
            if query:
                st.chat_message("user").write(query)
                with st.spinner("A analisar..."):
                    code = ask_gemini(df_final, query, api_key, business_context, file_names)
                    text, fig = execute_code(code, df_final)
                    if text: st.chat_message("assistant").write(text)
                    if fig and fig.get_fignums(): st.chat_message("assistant").pyplot(fig); fig.clf()
        elif file_names:
             st.error(file_names)

if __name__ == "__main__":
    main()