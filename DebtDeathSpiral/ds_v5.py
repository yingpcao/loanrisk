import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import copy

# ==========================================
# 0. 基础映射与智能推断配置
# ==========================================
st.set_page_config(page_title="PBoC债务螺旋模型V5.1", layout="wide", page_icon="🌪️")

ORG_CODE_MAP = {
    11: "商业银行", 12: "村镇银行", 14: "住房储蓄银行", 15: "外资银行",
    16: "财务公司", 21: "信托公司", 22: "融资租赁公司", 23: "汽车金融公司",
    24: "消费金融公司", 25: "贷款公司", 26: "金融资产管理公司",
    31: "证券公司", 41: "保险公司", 51: "小额贷款公司", 52: "公积金管理中心",
    53: "融资担保公司", 54: "保理公司", 99: "其他机构"
}

# 优化的层级映射
ORG_TIER_MAP = {
    11: 'T1', 14: 'T1', 15: 'T1', 52: 'T1', 
    12: 'T2', 16: 'T2', 21: 'T2', 23: 'T2', 24: 'T2', 31: 'T2', 41: 'T2', 
    22: 'T3', 25: 'T3', 26: 'T3', 51: 'T3', 53: 'T3', 54: 'T3', 99: 'T3' 
}

ACCT_TYPE_MAP = {
    'R1': 'Revolving', 'R2': 'Revolving', 'R4': 'Revolving',
    'D1': 'Fixed', 'R3': 'Fixed'
}

DEFAULT_RATES = {'T1': 0.08, 'T2': 0.18, 'T3': 0.24}

# ==========================================
# 1. 核心逻辑 (Loan, Market, Simulator)
# ==========================================

class Loan:
    def __init__(self, name, org_code, acct_type, limit, balance, monthly_pay, maturity_months, rate=None):
        self.name = name
        self.org_code = int(org_code)
        self.acct_type_code = acct_type
        self.tier = ORG_TIER_MAP.get(self.org_code, 'T3')
        self.logic_type = ACCT_TYPE_MAP.get(self.acct_type_code, 'Fixed')
        self.limit = float(limit)
        self.balance = float(balance)
        self.monthly_pay = float(monthly_pay)
        self.maturity = int(maturity_months)
        
        if rate is None or pd.isna(rate):
            self.rate = DEFAULT_RATES.get(self.tier, 0.24)
        else:
            self.rate = float(rate)

class Market:
    def __init__(self, config):
        self.cfg = config

    def get_offer(self, tier, income, current_counts):
        # 1. 污染阻断逻辑 (Toxic Block)
        # 如果客户持有T3类贷款 > 0，且当前申请的是T1(银行)，则直接拒贷
        if self.cfg['TOXIC_BLOCK'] and tier == 'T1' and current_counts.get('T3', 0) > 0:
            return 0, 0 
            
        count = current_counts.get(tier, 0)
        limit = 0
        base_rate = 0
        
        # 2. 额度与基准利率逻辑
        if tier == 'T1':
            if count >= self.cfg['MAX_ORGS_T1']: return 0, 0
            base_limit = income * self.cfg['LIMIT_MUL_T1']
            limit = base_limit * (self.cfg['DECAY_T1'] ** count)
            base_rate = self.cfg['BASE_RATE_T1']
            
        elif tier == 'T2':
            if count >= self.cfg['MAX_ORGS_T2']: return 0, 0
            base_limit = income * self.cfg['LIMIT_MUL_T2']
            limit = base_limit * (self.cfg['DECAY_T2'] ** count)
            base_rate = self.cfg['BASE_RATE_T2']
            
        elif tier == 'T3':
            if count >= self.cfg['MAX_ORGS_T3']: return 0, 0
            # T3 额度按指数衰减
            limit = max(self.cfg['LIMIT_START_T3'] * (self.cfg['DECAY_T3'] ** count), self.cfg['LIMIT_FLOOR_T3'])
            base_rate = self.cfg['BASE_RATE_T3']

        # 3. 利率多头惩罚
        rate = base_rate * (self.cfg['PENALTY_RATE'] ** count)
        rate = min(rate, 0.36) # 36% 封顶
        
        return round(limit, 2), round(rate, 4)

class DebtSpiralSimulator:
    def __init__(self, income, savings, living_cost, initial_loans, market_instance):
        self.income = income
        self.savings = savings
        self.living_cost = living_cost
        self.loans = initial_loans 
        self.market = market_instance
        self.month = 0
        self.is_dead = False
        self.death_reason = ""
        self.structured_logs = [] 
        self.history = [] 

    def log(self, category, event, amount=0, detail=""):
        self.structured_logs.append({
            "月份": self.month, "类别": category, "事件": event, "金额变动": amount, "详情": detail
        })

    def get_counts(self):
        counts = {'T1': 0, 'T2': 0, 'T3': 0}
        for loan in self.loans:
            if loan.balance > 0: 
                counts[loan.tier] += 1
        return counts

    def record_stats(self, gap=0):
        t1_debt = sum(l.balance for l in self.loans if l.tier == 'T1')
        t2_debt = sum(l.balance for l in self.loans if l.tier == 'T2')
        t3_debt = sum(l.balance for l in self.loans if l.tier == 'T3')
        self.history.append({
            'Month': self.month,
            'T1_Debt': t1_debt, 'T2_Debt': t2_debt, 'T3_Debt': t3_debt,
            'Total_Debt': t1_debt + t2_debt + t3_debt,
            'Savings': self.savings, 'Gap': gap
        })

    def run_month(self):
        self.month += 1
        total_payment = 0
        total_outflow = self.living_cost 
        self.log("支出", "生活费", -self.living_cost, "")

        # 偿还
        for loan in self.loans:
            if loan.balance > 0:
                total_outflow += loan.monthly_pay
                total_payment += loan.monthly_pay
                loan.maturity -= 1
        self.log("支出", "偿还月供", -total_payment, f"账户数: {len([l for l in self.loans if l.balance>0])}")

        # 本金到期
        matured_principal = 0
        matured_details = []
        for loan in self.loans:
            if loan.balance > 0 and loan.maturity <= 0:
                matured_principal += loan.balance
                matured_details.append(f"{loan.name}({loan.tier})")
                loan.balance = 0 
        
        if matured_principal > 0:
            self.log("冲击", "本金到期", -matured_principal, f"项目: {','.join(matured_details)}")
            total_outflow += matured_principal

        # 填坑
        net_flow = self.income - total_outflow
        self.log("收入", "工资", self.income, "")
        gap = 0
        
        if net_flow >= 0:
            self.savings += net_flow
            self.log("储蓄", "存入", net_flow, "")
        else:
            gap = abs(net_flow)
            self.log("预警", "缺口", -gap, "入不敷出")
            
            if self.savings > 0:
                used = min(self.savings, gap)
                self.savings -= used
                gap -= used
                self.log("填坑", "消耗存款", used, "")

            if gap > 1:
                for loan in self.loans:
                    if loan.logic_type == 'Revolving' and loan.balance < loan.limit:
                        draw = min(loan.limit - loan.balance, gap)
                        loan.balance += draw
                        gap -= draw
                        self.log("填坑", "以贷养贷", draw, f"从 {loan.name} 提现")
                        if gap < 1: break
            
            if gap > 1:
                funding_sources = ['T1', 'T2', 'T3']
                for source in funding_sources:
                    if gap < 1: break
                    while gap > 1:
                        counts = self.get_counts()
                        limit, rate = self.market.get_offer(source, self.income, counts)
                        
                        if limit <= 0: 
                            if source == 'T1' and counts.get('T3',0) > 0 and self.market.cfg['TOXIC_BLOCK']:
                                self.log("被拒", "T1风控", 0, "因持有T3被银行拒贷(污染效应)")
                            break 
                            
                        draw = min(limit, gap)
                        # 自动赋予代码: T1->11, T2->24, T3->51
                        code = 11 if source=='T1' else (24 if source=='T2' else 51)
                        new_loan = Loan(f"新{source}_{self.month}", code, 'R1', limit, draw, draw*0.03, 12, rate)
                        self.loans.append(new_loan)
                        gap -= draw
                        self.log("借新", "申请成功", draw, f"{source} | 息{rate:.1%}")

        self.record_stats(gap)
        if gap > 1:
            self.is_dead = True
            self.death_reason = f"资金链断裂 (缺口 {gap:.2f})"


# ==========================================
# 2. 数据加载与处理模块 (增强版)
# ==========================================
def load_and_parse_csv(file):
    # 定义尝试的编码列表：UTF-8 (通用), GBK (Excel默认), GB18030 (更全的中文集)
    encodings = ['utf-8', 'gbk', 'gb18030', 'utf-8-sig']
    
    df = pd.DataFrame()
    
    for enc in encodings:
        try:
            # 每次读取前必须重置文件指针到开头，否则第二次读取会读不到数据
            file.seek(0)
            
            # 尝试读取
            # 注意：如果您的CSV第一行是标题(如"客户号,机构..."), 请把 header=None 改为 header=0
            # 根据您上次提供的数据，看起来是有中文标题的，建议使用 header=0
            df = pd.read_csv(file, encoding=enc, header=0) 
            
            # 如果读取成功，打印调试信息
            # st.success(f"成功使用 {enc} 编码读取文件") 
            break 
            
        except UnicodeDecodeError:
            continue # 如果失败，尝试下一种编码
        except Exception as e:
            st.error(f"读取发生未知错误: {e}")
            return pd.DataFrame()
    
    if df.empty:
        st.error("无法识别文件编码，请尝试将CSV另存为 'UTF-8' 格式。")
        return pd.DataFrame()

    try:
        # 数据清洗：重命名列以匹配模型逻辑
        # 假设用户上传的CSV列顺序是固定的，按索引重命名
        # 您的数据示例：客户流水号,机构名称,机构代码,账户类型,额度,余额,月供,剩余期数
        # 对应模型：Client_ID, Name, Org_Code, Type, Limit, Balance, Payment, Months
        
        # 确保列数足够 (至少8列)
        if df.shape[1] >= 8:
            # 强制取前8列，防止有多余列报错
            df = df.iloc[:, :8] 
            df.columns = ["Client_ID", "Name", "Org_Code", "Type", "Limit", "Balance", "Payment", "Months"]
            return df
        else:
            st.error(f"CSV列数不足。需要8列，当前只有{df.shape[1]}列。")
            return pd.DataFrame()
            
    except Exception as e:
        st.error(f"数据解析/重命名失败: {e}")
        return pd.DataFrame()


# ==========================================
# 3. UI 界面
# ==========================================

st.title("🌪️ 个人债务螺旋模型 V5.1 (全参数实战版)")
st.caption("真实征信数据导入 + 全市场参数精细化配置")

# --- Sidebar: 数据与画像 ---
with st.sidebar:
    st.header("1. 数据导入 (Data Import)")
    uploaded_file = st.file_uploader("上传真实征信明细 (CSV)", type=["csv"])
    
    selected_client_loans = []
    
    if uploaded_file:
        df_raw = load_and_parse_csv(uploaded_file)
        if not df_raw.empty:
            clients = df_raw["Client_ID"].unique()
            client_id = st.selectbox("选择客户 (Client ID)", clients)
            
            client_data = df_raw[df_raw["Client_ID"] == client_id].copy()
            st.info(f"已加载 {len(client_data)} 笔信贷记录")
            st.dataframe(client_data[["Name", "Org_Code", "Balance", "Months"]].head(3), height=100)
            
            for _, row in client_data.iterrows():
                selected_client_loans.append(Loan(
                    row["Name"], row["Org_Code"], row["Type"],
                    row["Limit"], row["Balance"], row["Payment"], 
                    row["Months"]
                ))
    else:
        st.info("👈 请先在左侧上传CSV文件")

    st.divider()
    st.header("2. 客户画像 (Profile)")
    income = st.number_input("月收入", 0, 100000, 15000, 1000)
    savings = st.number_input("初始存款", 0, 1000000, 5000, 1000)
    living_cost = st.number_input("刚性生活费", 0, 50000, 3500, 500)
    
    run_btn = st.button("🚀 开始推演", type="primary", use_container_width=True)

# --- Main: 市场风控配置 (恢复全参数) ---
with st.expander("⚙️ 市场风控与模型参数配置 (Market & Risk Settings)", expanded=True):
    
    # 使用 Tab 分组，整洁展示参数
    tab_global, tab_t1, tab_t2, tab_t3 = st.tabs(["🌍 全局策略", "🏦 T1 银行配置", "🏢 T2 消金配置", "🧨 T3 网贷配置"])
    
    with tab_global:
        col_g1, col_g2 = st.columns(2)
        with col_g1:
            toxic_block = st.checkbox("🚫 启用「T3污染阻断」", value=True, 
                help="真实风控逻辑：若客户持有T3(小贷)余额，银行(T1)直接拒贷。")
        with col_g2:
            penalty = st.slider("📉 多头惩罚系数 (Penalty)", 1.0, 2.0, 1.15, 0.05, 
                help="每多一家机构，利率上浮倍数")
            
    with tab_t1:
        c1, c2, c3, c4 = st.columns(4)
        with c1: max_orgs_t1 = st.number_input("T1 最大机构数", 1, 20, 2, help="超过此数量银行拒贷")
        with c2: limit_mul_t1 = st.number_input("T1 收入倍数", 1, 50, 12, help="授信额度锚点")
        with c3: decay_t1 = st.slider("T1 多头衰减", 0.1, 1.0, 0.90, 0.05)
        with c4: base_rate_t1 = st.number_input("T1 基准利率", 0.01, 0.36, 0.08, 0.01)

    with tab_t2:
        c1, c2, c3, c4 = st.columns(4)
        with c1: max_orgs_t2 = st.number_input("T2 最大机构数", 1, 20, 5)
        with c2: limit_mul_t2 = st.number_input("T2 收入倍数", 1, 30, 4)
        with c3: decay_t2 = st.slider("T2 多头衰减", 0.1, 1.0, 0.85, 0.05)
        with c4: base_rate_t2 = st.number_input("T2 基准利率", 0.01, 0.36, 0.18, 0.01)

    with tab_t3:
        c1, c2, c3, c4 = st.columns(4)
        with c1: max_orgs_t3 = st.number_input("T3 最大机构数", 1, 50, 20)
        with c2: start_limit_t3 = st.number_input("T3 起始额度", 1000, 100000, 30000, 1000)
        with c3: decay_t3 = st.slider("T3 断崖衰减", 0.1, 1.0, 0.50, 0.05)
        with c4: base_rate_t3 = st.number_input("T3 基准利率", 0.01, 0.36, 0.24, 0.01)

    # 组装配置字典
    market_config = {
        'TOXIC_BLOCK': toxic_block, 'PENALTY_RATE': penalty,
        'MAX_ORGS_T1': max_orgs_t1, 'LIMIT_MUL_T1': limit_mul_t1, 'DECAY_T1': decay_t1, 'BASE_RATE_T1': base_rate_t1,
        'MAX_ORGS_T2': max_orgs_t2, 'LIMIT_MUL_T2': limit_mul_t2, 'DECAY_T2': decay_t2, 'BASE_RATE_T2': base_rate_t2,
        'MAX_ORGS_T3': max_orgs_t3, 'LIMIT_START_T3': start_limit_t3, 'LIMIT_FLOOR_T3': 2000, 'DECAY_T3': decay_t3, 'BASE_RATE_T3': base_rate_t3,
    }

# --- Execution ---
if run_btn and selected_client_loans:
    
    # 1. 静态分析 (Maturity Wall)
    st.markdown("### 📊 1. 静态压力测试 (Static Analysis)")
    
    maturity_data = {}
    for l in selected_client_loans:
        if l.balance > 0:
            maturity_data[l.maturity] = maturity_data.get(l.maturity, 0) + l.balance
    
    months_range = list(range(1, 25))
    amounts = [maturity_data.get(m, 0) for m in months_range]
    
    fig_wall = px.bar(
        x=months_range, y=amounts,
        title="⚠️ 债务到期墙 (Maturity Wall) - 未来24个月本金偿还洪峰",
        labels={'x': '未来月份 (Month)', 'y': '需偿还本金 (Principal)'},
        color=amounts, color_continuous_scale='Reds'
    )
    # 增加月收入参考线
    fig_wall.add_hline(y=income, line_dash="dash", line_color="#27ae60", annotation_text="月收入线 (Income)")
    st.plotly_chart(fig_wall, use_container_width=True)
    
    # 2. 动态推演 (Dynamic Simulation)
    st.divider()
    st.markdown("### 🌪️ 2. 动态死亡推演 (Dynamic Simulation)")
    
    market = Market(market_config)
    sim_loans = copy.deepcopy(selected_client_loans)
    sim = DebtSpiralSimulator(income, savings, living_cost, sim_loans, market)
    
    for i in range(24):
        if sim.is_dead: break
        sim.run_month()
        
    # KPI
    k1, k2, k3 = st.columns(3)
    final_debt = sum(l.balance for l in sim.loans)
    with k1:
        if sim.is_dead: st.error(f"❌ 确认违约: Month {sim.month}")
        else: st.success("✅ 幸存: 24 Months")
    with k2: st.metric("期末总负债", f"¥{final_debt:,.0f}")
    with k3: 
        if sim.is_dead: st.warning(f"💀 死因: {sim.death_reason}")
    
    # Tabs
    res_t1, res_t2 = st.tabs(["📉 债务与资金流趋势", "📋 详细审计日志"])
    
    with res_t1:
        df_h = pd.DataFrame(sim.history)
        if not df_h.empty:
            # 债务堆叠图
            fig_stack = px.area(
                df_h, x="Month", y=["T1_Debt", "T2_Debt", "T3_Debt"],
                title="债务结构堆叠 (Debt Structure)",
                color_discrete_map={"T1_Debt": "#3498db", "T2_Debt": "#f39c12", "T3_Debt": "#e74c3c"}
            )
            st.plotly_chart(fig_stack, use_container_width=True)
            
            # 流动性图
            fig_liq = go.Figure()
            fig_liq.add_trace(go.Scatter(x=df_h['Month'], y=df_h['Savings'], name='存款余额', line=dict(color='green', width=3)))
            fig_liq.add_trace(go.Bar(x=df_h['Month'], y=df_h['Gap'], name='资金缺口', marker_color='red'))
            fig_liq.update_layout(title="流动性生存线 (Liquidity & Gap)", barmode='stack')
            st.plotly_chart(fig_liq, use_container_width=True)

    with res_t2:
        df_l = pd.DataFrame(sim.structured_logs)
        st.dataframe(
            df_l.style.format({"金额变动": "{:,.2f}"})
            .map(lambda x: 'color:red' if x<0 else 'color:green', subset=['金额变动']),
            use_container_width=True
        )

elif run_btn:
    st.warning("请先上传 CSV 文件数据。")