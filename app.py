import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import google.generativeai as genai
import sys
from io import StringIO

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Data AI Pro", page_icon="📊", layout="wide")

# --- 1. FUNÇÃO DE LIMPEZA ---
def load_and_clean(file):
    try:
        if file.name.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
        
        # Limpeza básica
        df.drop_duplicates(inplace=True)
        
        # Preenchimento inteligente
        for col in df.columns:
            if df[col].dtype == 'object':
                # Tenta converter para data primeiro
                try:
                    df[col] = pd.to_datetime(df[col])
                except:
                    # Se falhar, trata como categoria
                    df[col] = df[col].fillna(df[col].mode()[0] if not df[col].mode().empty else "Desconhecido")
            else:
                df[col] = df[col].fillna(df[col].median())
        return df
    except Exception as e:
        st.error(f"Erro ao ler ficheiro: {e}")
        return None

# --- 2. CÉREBRO GEMINI (AUTO-DETECT) ---
def ask_gemini_for_code(df, query, api_key):
    genai.configure(api_key=api_key)
    
    # Tenta descobrir o modelo disponível
    chosen_model = "gemini-1.5-flash"
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'flash' in m.name:
                    chosen_model = m.name
                    break
                elif 'pro' in m.name:
                    chosen_model = m.name
    except:
        pass 

    model = genai.GenerativeModel(chosen_model)
    
    # Contexto para a IA
    columns_info = df.dtypes.to_string()
    head_info = df.head(3).to_string()

    prompt = f"""
    Você é um assistente de Data Science em Python.
    Tenho um dataframe pandas chamado 'df'.
    Estrutura: {columns_info}
    Amostra: {head_info}
    
    O usuário quer: "{query}"
    
    Escreva código Python para resolver.
    REGRAS:
    1. Use 'df' diretamente.
    2. 2. Se o resultado for uma tabela, use 'print(df.to_markdown())' para formatar bem.
    3. Use 'plt.figure()' para gráficos (NÃO use plt.show()).
    4. APENAS CÓDIGO. Sem markdown.
    """
    
    response = model.generate_content(prompt)
    return response.text.replace("```python", "").replace("```", "").strip()

# --- 3. EXECUTOR DE CÓDIGO ---
def execute_generated_code(code, df):
    try:
        old_stdout = sys.stdout
        redirected_output = sys.stdout = StringIO()
        local_vars = {'df': df, 'plt': plt, 'sns': sns, 'pd': pd}
        
        exec(code, {}, local_vars)
        
        sys.stdout = old_stdout
        text_output = redirected_output.getvalue()
        return text_output, plt
    except Exception as e:
        return f"Erro: {e}", None

# --- 4. INTERFACE PRINCIPAL ---
def main():
    # --- BARRA LATERAL ---
    # --- BARRA LATERAL (Inteligente) ---
    with st.sidebar:
        st.title("💼 Assistente **Pro**")
        st.markdown("---")
        
        # Tenta buscar a chave nos Segredos do Streamlit
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.success("✅ API Key cready for Take-off")
        else:
            # Se não estiver nos segredos (ex: rodando localmente), pede ao usuário
            api_key = st.text_input("Gemini API Key", type="password")
            st.markdown("[Obter API Key](https://aistudio.google.com/app/apikey)")
        
        st.markdown("---")
        st.info("💡 Dica: Pergunte sobre vendas, tendências ou peça gráficos.")
        st.caption("v2.1 - Insightkube")

    # --- TÍTULO PRINCIPAL ---
    st.markdown("""
        <style>
        .title-font { font-size:40px; font-weight: bold; color: #4CAF50; }
        .subtitle-font { font-size:20px; color: #6C757D; }
        </style>
        <p class="title-font">📈 Análise de Dados Inteligente</p>
        <p class="subtitle-font">O seu cientista de dados pessoal, powered by Insightkube.</p>
    """, unsafe_allow_html=True)

    # --- UPLOAD ---
    uploaded_file = st.file_uploader("Carregue seu Excel/ CSV e veja a magia a acontecer!", type=['csv', 'xlsx'])

    if uploaded_file and api_key:
        # Carregar dados
        if 'df' not in st.session_state or st.session_state.get('fname') != uploaded_file.name:
            st.session_state['df'] = load_and_clean(uploaded_file)
            st.session_state['fname'] = uploaded_file.name

        df = st.session_state['df']
        
        # --- RELATÓRIO PRELIMINAR ---
        with st.expander("📊 Relatório Preliminar dos Dados", expanded=True):
          
            tab1, tab2, tab3 = st.tabs(["📋 Tabela", "📈 Estatísticas", "🔥 Correlações"])
            
            with tab1:
                st.markdown("**Primeiras 10 linhas:**")
                st.dataframe(df.head(10), use_container_width=True)
            
            with tab2:
                st.markdown("**Resumo Estatístico:**")
                st.dataframe(df.describe(), use_container_width=True)
                
            with tab3:
                st.markdown("**Mapa de Calor (Heatmap):**")
                numeric_df = df.select_dtypes(include=['float64', 'int64'])
                if len(numeric_df.columns) > 1:
                    fig, ax = plt.subplots(figsize=(8, 5))
                    sns.heatmap(numeric_df.corr(), annot=True, cmap='Blues', fmt=".2f", ax=ax)
                    st.pyplot(fig)
                else:
                    st.warning("Preciso de mais colunas numéricas para gerar correlações.")

        # --- CHAT ---
        st.markdown("---")
        st.subheader("💬 Chat com os Dados")
        
        query = st.chat_input("Ex: Faça um gráfico de barras das vendas por região...")
        
        if query:
            st.chat_message("user").write(query)
            
            with st.spinner("🤖 A analisar..."):
                code = ask_gemini_for_code(df, query, api_key)
                text_result, plot_result = execute_generated_code(code, df)
                
                if text_result:
                    st.chat_message("assistant").write(text_result)
                
                if plot_result and plot_result.get_fignums():
                    st.chat_message("assistant").pyplot(plot_result)
                    plot_result.clf()

    # Rodapé
    st.markdown("---")
    st.caption("© 2025 Projeto AI Insightkube. Todos os direitos reservados.")

if __name__ == "__main__":
    main()