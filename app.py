import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import google.generativeai as genai
import sys
from io import StringIO
import re  # <--- Nova ferramenta para limpar o código

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="AI Data Hub - Final", page_icon="🚀", layout="wide")

# --- 1. LIMPEZA E FUSÃO (MANTIDO IGUAL) ---
from functools import reduce

def clean_individual_df(df, filename):
    df.drop_duplicates(inplace=True)
    date_col = None
    
    # Procura data
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            date_col = col
            break
    if not date_col:
        for col in df.columns:
            if df[col].dtype == 'object':
                try:
                    pd.to_datetime(df[col])
                    df[col] = pd.to_datetime(df[col])
                    date_col = col
                    break
                except: pass

    if date_col:
        df.rename(columns={date_col: 'DATA_FUSAO'}, inplace=True)
        return df, True
    else:
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
                cols_novas = {c: f"{prefix}_{c}" for c in clean_df.columns if c != 'DATA_FUSAO'}
                clean_df.rename(columns=cols_novas, inplace=True)
                dataframes.append(clean_df)
                file_names.append(file.name)
        except Exception as e:
            st.error(f"Erro em {file.name}: {e}")

    if not dataframes: return None, "Sem ficheiros."

    try:
        if len(dataframes) == 1: return dataframes[0], file_names
        df_final = reduce(lambda left, right: pd.merge(left, right, on='DATA_FUSAO', how='outer'), dataframes)
        df_final = df_final.sort_values('DATA_FUSAO').fillna(0)
        return df_final, file_names
    except Exception as e: return None, str(e)

# --- 2. CÉREBRO BLINDADO (A CORREÇÃO ESTÁ AQUI) ---
def ask_gemini(df, query, api_key, context, file_list):
    genai.configure(api_key=api_key)
    
    # Escolha do modelo
    chosen_model = "gemini-1.5-flash"
    try:
        for m in genai.list_models():
            if 'flash' in m.name: chosen_model = m.name; break
            elif 'pro' in m.name: chosen_model = m.name
    except: pass 
    
    model = genai.GenerativeModel(chosen_model)
    
    prompt = f"""
    Atue como Programador Python Sênior e Analista de Dados.
    CONTEXTO: {context}
    DADOS: {", ".join(file_list)}
    ESTRUTURA: {df.dtypes.to_string()}
    PERGUNTA: "{query}"
    
    REGRAS RÍGIDAS:
    1. Escreva APENAS código Python dentro de blocos ```python ... ```.
    2. NÃO use o símbolo '€' fora de strings (aspas). Errado: x = 10€. Certo: print("10€").
    3. Use 'df' (coluna tempo: 'DATA_FUSAO').
    4. Gráficos: plt.figure(), plote e NÃO use plt.show().
    5. Se for preciso calcular correlações, use df.corr() e explique com print().
    """
    
    response = model.generate_content(prompt)
    texto_resposta = response.text
    
    # --- FILTRO INTELIGENTE (REGEX) ---
    # Procura apenas o que está dentro de ```python ... ```
    padrao = r"```python(.*?)```"
    match = re.search(padrao, texto_resposta, re.DOTALL)
    
    if match:
        code = match.group(1).strip()
    else:
        # Se a IA esqueceu os blocos, tenta usar o texto todo mas limpa perigos
        code = texto_resposta.replace("```", "").strip()
        
    return code

# --- 3. EXECUTOR ---
def execute_code(code, df):
    try:
        import numpy as np # Garantir que numpy está disponível
        old_stdout = sys.stdout
        redirected_output = sys.stdout = StringIO()
        local_vars = {'df': df, 'plt': plt, 'sns': sns, 'pd': pd, 'np': np}
        exec(code, {}, local_vars)
        sys.stdout = old_stdout
        return redirected_output.getvalue(), plt
    except Exception as e:
        return f"Erro no código: {e}\n\nCódigo tentado:\n{code}", None

# --- 4. INTERFACE ---
def main():
    st.title("🚀 AI Data Hub: Fusão Inteligente")
    
    with st.sidebar:
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.success("Chave OK")
        else:
            api_key = st.text_input("API Key", type="password")
        st.markdown("---")
        business_context = st.text_area("Contexto", height=100)

    uploaded_files = st.file_uploader("Carregue ficheiros", accept_multiple_files=True)

    if uploaded_files and api_key:
        df_final, file_names = smart_merge(uploaded_files)
        
        if df_final is not None:
            st.success(f"✅ Dados conectados: {', '.join(file_names)}")
            with st.expander("Ver Dados"): st.dataframe(df_final.head())

            query = st.chat_input("Faça a sua pergunta...")
            if query:
                st.chat_message("user").write(query)
                with st.spinner("A analisar correlações..."):
                    code = ask_gemini(df_final, query, api_key, business_context, file_names)
                    text, fig = execute_code(code, df_final)
                    
                    if text: st.chat_message("assistant").write(text)
                    if fig and fig.get_fignums(): st.chat_message("assistant").pyplot(fig); fig.clf()
        elif file_names: st.error(file_names)

if __name__ == "__main__":
    main()