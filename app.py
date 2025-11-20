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
# --- NOVA IMPORTAÇÃO NO TOPO DO FICHEIRO ---
from sklearn.linear_model import LinearRegression
import numpy as np

# --- FUNÇÃO DE ML (PREVISÃO) ---
def run_ml_forecast(df):
    try:
        # 1. Preparar Dados: Agrupar por Data
        # Procura colunas de data e valor
        date_col = None
        val_col = None
        
        for c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                date_col = c
            elif pd.api.types.is_numeric_dtype(df[c]) and "id" not in c.lower():
                val_col = c
        
        if not date_col or not val_col:
            return "Não encontrei colunas de Data ou Valor numérico suficientes para previsão.", None

        # Agrupar vendas por dia
        df_ml = df.groupby(date_col)[val_col].sum().reset_index()
        df_ml = df_ml.sort_values(date_col)

        # 2. Engenharia de Features (Data -> Número)
        df_ml['Date_Num'] = df_ml[date_col].map(pd.Timestamp.toordinal)
        
        X = df_ml[['Date_Num']] # Features (Tempo)
        y = df_ml[val_col]      # Target (Vendas)

        # 3. Treinar o Modelo (Linear Regression)
        model = LinearRegression()
        model.fit(X, y)

        # 4. Prever Futuro (30 dias)
        last_date = df_ml['Date_Num'].max()
        future_dates_num = np.array([last_date + i for i in range(1, 31)]).reshape(-1, 1)
        future_sales = model.predict(future_dates_num)

        # 5. Criar Gráfico
        fig, ax = plt.subplots(figsize=(10, 5))
        
        # Dados Reais (Pontos Azuis)
        ax.scatter(df_ml[date_col], y, color='blue', label='Dados Reais')
        
        # Linha de Tendência (Vermelha)
        ax.plot(df_ml[date_col], model.predict(X), color='red', linestyle='--', label='Tendência Atual')
        
        # Previsão Futura (Pontos Verdes)
        future_dates = [pd.Timestamp.fromordinal(int(d[0])) for d in future_dates_num]
        ax.plot(future_dates, future_sales, color='green', linewidth=2, label='Previsão ML (30 dias)')
        
        ax.set_title(f"Previsão de Machine Learning: {val_col} vs Tempo")
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        total_predicted = sum(future_sales)
        
        return f"O modelo de ML prevê um total de **{total_predicted:,.2f}** para os próximos 30 dias.", fig

    except Exception as e:
        return f"Erro no ML: {e}", None


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
            # --- SECÇÃO DE MACHINE LEARNING ---
        st.markdown("### 🔮 Previsão do Futuro (Machine Learning)")
        
        col1, col2 = st.columns([1, 3])
        
        with col1:
            if st.button("Treinar Modelo de IA"):
                with st.spinner("A treinar regressão linear..."):
                    ml_text, ml_fig = run_ml_forecast(df)
                    
                    if ml_fig:
                        st.success("Modelo treinado com sucesso!")
                        # Guardar na sessão para não sumir
                        st.session_state['ml_fig'] = ml_fig
                        st.session_state['ml_text'] = ml_text
                    else:
                        st.warning(ml_text)

        with col2:
            # Se já existir previsão, mostra
            if 'ml_fig' in st.session_state:
                st.write(st.session_state['ml_text'])
                st.pyplot(st.session_state['ml_fig'])

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