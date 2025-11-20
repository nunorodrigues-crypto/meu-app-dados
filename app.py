import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import google.generativeai as genai
import sys
from io import StringIO

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="AI Strategic Analyst", page_icon="🧠", layout="wide")

# --- 1. FUNÇÃO DE LIMPEZA ---
def load_and_clean(file):
    try:
        if file.name.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
        
        df.drop_duplicates(inplace=True)
        
        for col in df.columns:
            if df[col].dtype == 'object':
                try:
                    df[col] = pd.to_datetime(df[col])
                except:
                    df[col] = df[col].fillna(df[col].mode()[0] if not df[col].mode().empty else "Desconhecido")
            else:
                df[col] = df[col].fillna(df[col].median())
        return df
    except Exception as e:
        st.error(f"Erro ao ler ficheiro: {e}")
        return None

# --- 2. CÉREBRO GEMINI COM CONTEXTO ---
def ask_gemini_for_code(df, query, api_key, context):
    genai.configure(api_key=api_key)
    
    # Escolha do modelo (Tenta Pro, falha para Flash)
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

    # PROMPT BLINDADO CONTRA TEXTO SOLTO
    prompt = f"""
    Você é um Consultor Estratégico e Programador Python Expert.
    
    CONTEXTO DO CLIENTE:
    {context}
    
    DADOS (df):
    {columns_info}
    {head_info}
    
    PERGUNTA: "{query}"
    
    Sua missão é escrever um script Python que analise os dados e imprima a resposta.
    
    REGRAS DE OURO (OBRIGATÓRIAS):
    1. Todo e qualquer texto explicativo DEVE estar dentro de um print(). 
       Exemplo CORRETO: print(f"A média foi {{media}}...")
       Exemplo ERRADO: A média foi... (Isso quebra o código).
    
    2. Para análises longas ou com várias linhas, use print(''' SEU TEXTO AQUI ''') com aspas triplas.
    
    3. Primeiro calcule os números usando pandas (use a variável 'df').
    4. Depois, faça a análise crítica misturando os números com o CONTEXTO DO CLIENTE.
    5. Não use input(). Não use markdown (```). Retorne apenas o código puro.
    """
    
    response = model.generate_content(prompt)
    
    # Limpeza extra para garantir que não sobra lixo
    code = response.text.replace("```python", "").replace("```", "").strip()
    return code

# --- 3. EXECUTOR ---
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
        # Mostra o erro E o código que causou o erro
        return f"❌ Erro de Execução: {e}\n\n🔍 Código Gerado pela IA (Debug):\n{code}", None
    
# --- 4. INTERFACE ---
def main():
    with st.sidebar:
        st.title("🧠 Analista Estratégico")
        
        # API Key Segura
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.success("Chave carregada!")
        else:
            api_key = st.text_input("Gemini API Key", type="password")
        
        st.markdown("---")
        
        # --- CAMPO NOVO: O CONTEXTO ---
        st.subheader("🏢 Definição do Negócio")
        business_context = st.text_area(
            "Quem é a empresa? (Fundamental para análise causal)",
            placeholder="Ex: Somos uma geladaria no Algarve. O nosso pico é no Verão. Temos concorrência forte em Agosto...",
            height=150
        )
        st.info("Quanto mais detalhes der aqui, mais inteligente será a análise de causalidade.")

    st.markdown("## 📊 Dashboard & Inteligência de Negócio")

    uploaded_file = st.file_uploader("Carregue os dados (Excel/CSV)", type=['csv', 'xlsx'])

    if uploaded_file and api_key:
        if 'df' not in st.session_state or st.session_state.get('fname') != uploaded_file.name:
            st.session_state['df'] = load_and_clean(uploaded_file)
            st.session_state['fname'] = uploaded_file.name

        df = st.session_state['df']
        
        with st.expander("Ver Tabela de Dados", expanded=False):
            st.dataframe(df.head())

        # Chat
        query = st.chat_input("Pergunte sobre KPIs, tendências ou causas...")
        
        if query:
            st.chat_message("user").write(query)
            
            if not business_context:
                st.warning("⚠️ Atenção: Sem preencher o 'Contexto do Negócio' na barra lateral, a IA só fará contas matemáticas, sem análise estratégica.")
            
            with st.spinner("🧠 A cruzar dados com estratégia de negócio..."):
                # Passamos o contexto para a função
                code = ask_gemini_for_code(df, query, api_key, business_context)
                text_result, plot_result = execute_generated_code(code, df)
                
                if text_result:
                    st.chat_message("assistant").write(text_result)
                
                if plot_result and plot_result.get_fignums():
                    st.chat_message("assistant").pyplot(plot_result)
                    plot_result.clf()

if __name__ == "__main__":
    main()