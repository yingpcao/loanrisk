import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ==========================================
# 0. 基础映射与配置
# ==========================================
st.set_page_config(page_title="PBoC债务螺旋模型V4", layout="wide", page_icon="🌪️")

ORG_CODE_MAP = {
    11: "商业银行", 12: "村镇银行", 14: "住房储蓄银行", 15: "外资银行",
    16: "财务公司", 21: "信托公司", 22: "融资租赁公司", 23: "汽车金融公司",
    24: "消费金融公司", 25: "贷款公司", 26: "金融资产管理公司",
    31: "证券公司", 41: "保险公司", 51: "小额贷款公司", 52: "公积金管理中心",
    53: "融资担保公司", 54: "保理公司", 99: "其他机构"
}

ORG_TIER_MAP = {
    11: 'T1', 12: 'T1', 14: 'T1', 15: 'T1', 52: 'T1',
    16: 'T2', 23: 'T2', 24: 'T2', 21: 'T2', 31: 'T2', 41: 'T2',
    51: 'T3', 53: 'T3', 54: 'T3', 22: 'T3', 25: 'T3', 26: 'T3', 99: 'T3'
}

ACCT_TYPE_MAP = {
    'R1': 'Revolving', 'R2': 'Revolving', 'R4': 'Revolving',
    'D1': 'Fixed', 'R3': 'Fixed'
}

# ==========================================
# 1. 核心逻辑 (保持不变)
# ==========================================

class Loan:
    def __init__(self, name, org_code, acct_type, limit, balance, monthly_pay, maturity_months, rate):
        self.name = name
        self.org_code = int(org_code)
        self.acct_type_code = acct_type
        self.tier = ORG_TIER_MAP.get(self.org_code, 'T3')
        self.logic_type = ACCT_TYPE_MAP.get(self.acct_type_code, 'Fixed')
        self.limit = float(limit)
        self.balance = float(balance)
        self.monthly_pay = float(monthly_pay)
        self.maturity = int(maturity_months)
        self.rate = float(rate)

class Market:
    def __init__(self, config):
        self.cfg = config

    def get_limit_and_rate(self, tier, income, count):
        limit = 0
        base_rate = 0
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
            limit = max(self.cfg['LIMIT_START_T3'] * (self.cfg['DECAY_T3'] ** count), self.cfg['LIMIT_FLOOR_T3'])
            base_rate = self.cfg['BASE_RATE_T3']

        rate = base_rate * (self.cfg['PENALTY_RATE'] ** count)
        rate = min(rate, 0.36)
        return round(limit, 2), round(rate, 4)

    def get_offer(self, tier, income, current_counts):
        count = current_counts.get(tier, 0)
        return self.get_limit_and_rate(tier, income, count)

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
        self.log("支出", "生活费", -self.living_cost, "基础生存成本")

        # 1. 偿还
        for loan in self.loans:
            if loan.balance > 0:
                total_outflow += loan.monthly_pay
                total_payment += loan.monthly_pay
                loan.maturity -= 1
        self.log("支出", "偿还月供", -total_payment, f"偿还 {len([l for l in self.loans if l.balance>0])} 笔")

        # 2. 到期
        matured_principal = 0
        matured_loans = []
        for loan in self.loans:
            if loan.balance > 0 and loan.maturity <= 0:
                matured_principal += loan.balance
                matured_loans.append(f"{loan.name}")
                loan.balance = 0 
        
        if matured_principal > 0:
            self.log("冲击", "本金到期", -matured_principal, f"到期: {','.join(matured_loans)}")
            total_outflow += matured_principal

        # 3. 填坑
        net_flow = self.income - total_outflow
        self.log("收入", "工资入账", self.income, "")
        gap = 0
        
        if net_flow >= 0:
            self.savings += net_flow
            self.log("储蓄", "存入余钱", net_flow, "盈余")
        else:
            gap = abs(net_flow)
            self.log("预警", "现金流缺口", -gap, "入不敷出")
            
            if self.savings > 0:
                used = min(self.savings, gap)
                self.savings -= used
                gap -= used
                self.log("填坑", "消耗存款", used, f"剩 {self.savings:.0f}")

            if gap > 1:
                for loan in self.loans:
                    if loan.logic_type == 'Revolving' and loan.balance < loan.limit:
                        draw = min(loan.limit - loan.balance, gap)
                        loan.balance += draw
                        gap -= draw
                        self.log("填坑", "以贷养贷", draw, f"从[{loan.name}]提现")
                        if gap < 1: break
            
            if gap > 1:
                funding_sources = ['T1', 'T2', 'T3']
                for source in funding_sources:
                    if gap < 1: break
                    while gap > 1:
                        counts = self.get_counts()
                        limit, rate = self.market.get_offer(source, self.income, counts)
                        if limit <= 0: 
                            self.log("被拒", "申请拒绝", 0, f"{source}风控拒绝")
                            break 
                        draw = min(limit, gap)
                        dummy_code = 11 if source == 'T1' else (24 if source == 'T2' else 51)
                        new_loan = Loan(f"新{source}_{self.month}", dummy_code, 'R1', limit, draw, draw*0.03, 12, rate)
                        self.loans.append(new_loan)
                        gap -= draw
                        self.log("借新", "申请成功", draw, f"{source}|额度{limit:.0f}|息{rate:.1%}")

        self.record_stats(gap)
        if gap > 1:
            self.is_dead = True
            self.death_reason = f"资金链断裂 (缺口 {gap:.2f})"

# ==========================================
# 2. UI Layout & Logic
# ==========================================

st.title("🌪️ 个人债务螺旋死亡模型 (Debt Spiral V4.0)")
st.caption("基于央行征信数据结构的流动性压力测试系统 | 优化交互版")

# --- 侧边栏：客户基本面与负债表 ---
with st.sidebar:
    st.header("1. 客户基本面 (Profile)")
    income = st.number_input("月收入 (Income)", 5000, 100000, 12000, 1000, help="税后实发工资")
    savings = st.number_input("初始存款 (Savings)", 0, 1000000, 15000, 5000, help="可随时动用的现金")
    living_cost = st.number_input("刚性生活费 (Living Cost)", 1000, 50000, 3500, 500, help="吃饭、房租、交通等物理生存成本")
    
    st.divider()
    
    st.header("2. 存量债务 (Trade List)")
    st.caption("默认展示5条典型多头借贷记录")
    
    # -----------------------------------------------------
    # 优化点1：演示数据增加到5条，覆盖不同类型
    # -----------------------------------------------------
    default_data = [
        {"名称": "招商银行信用卡", "机构代码": 11, "账户类型": "R2", "额度": 60000, "余额": 58000, "月供": 6000, "到期月数": 6, "利率": 0.15},
        {"名称": "建设银行快贷",   "机构代码": 11, "账户类型": "D1", "额度": 100000, "余额": 80000, "月供": 3500, "到期月数": 24, "利率": 0.06},
        {"名称": "蚂蚁借呗",       "机构代码": 24, "账户类型": "D1", "额度": 40000, "余额": 38000, "月供": 3200, "到期月数": 12, "利率": 0.18},
        {"名称": "微众微粒贷",     "机构代码": 12, "账户类型": "R4", "额度": 30000, "余额": 28000, "月供": 2800, "到期月数": 10, "利率": 0.16},
        {"名称": "度小满(有钱花)", "机构代码": 51, "账户类型": "D1", "额度": 20000, "余额": 15000, "月供": 1500, "到期月数": 12, "利率": 0.24}
    ]
    
    uploaded_file = st.file_uploader("导入CSV (可选)", type=["csv"])
    if uploaded_file:
        try:
            initial_df = pd.read_csv(uploaded_file)
        except:
            st.error("CSV读取失败")
            initial_df = pd.DataFrame(default_data)
    else:
        initial_df = pd.DataFrame(default_data)

    edited_df = st.data_editor(
        initial_df, num_rows="dynamic",
        column_config={
            "机构代码": st.column_config.SelectboxColumn("机构", options=list(ORG_CODE_MAP.keys()), width="small"),
            "账户类型": st.column_config.SelectboxColumn("类型", options=list(ACCT_TYPE_MAP.keys()), width="small"),
            "额度": st.column_config.NumberColumn("额度", format="%d"),
            "余额": st.column_config.NumberColumn("余额", format="%d"),
            "利率": st.column_config.NumberColumn("利率", format="%.2f")
        },
        use_container_width=True
    )
    
    st.divider()
    run_btn = st.button("🚀 开始推演 (Run Simulation)", type="primary", use_container_width=True)

# --- 主界面：市场配置区域 (优化版) ---
# 使用 Expander 包裹，但内部布局优化
with st.expander("⚙️ 市场风控参数配置 (Market Risk Engine)", expanded=True):
    
    # -----------------------------------------------------
    # 优化点2：布局优化 - 顶部放置图表与全局参数，下部放置Tab
    # -----------------------------------------------------
    
    # 顶部：图表预览区 (占据主要视觉) + 全局配置 (左侧)
    top_col1, top_col2 = st.columns([1, 2])
    
    with top_col1:
        st.markdown("#### 🌍 全局参数")
        st.info("设置宏观环境的风控严苛程度")
        penalty_rate = st.number_input("📉 多头惩罚系数 (Penalty)", 1.0, 2.0, 1.1, 0.05, 
                                       help="每多一家机构，利率上浮的倍数 (指数级)")
        
        st.markdown("#### 📊 图表说明")
        st.caption("""
        右侧图表实时展示了在当前配置下，
        随着**持有机构数量(Count)**的增加，
        市场给予的**额度(Limit)**和**利率(Rate)**的变化趋势。
        """)

    # 下部：Tab 分组配置 (T1/T2/T3)
    # 将配置项存入变量，稍后用于绘图
    
    st.markdown("---")
    st.markdown("#### 🏢 分层级风控配置")
    
    tabs = st.tabs(["🏦 T1 银行 (主力)", "🏢 T2 消金 (次级)", "🧨 T3 网贷 (尾部)"])
    
    with tabs[0]: # T1 Tab
        c_t1_1, c_t1_2, c_t1_3, c_t1_4 = st.columns(4)
        with c_t1_1: max_orgs_t1 = st.number_input("T1 最大机构数", 0, 20, 2, help="超过此数量直接拒贷")
        with c_t1_2: base_rate_t1 = st.number_input("T1 基准利率", 0.01, 0.36, 0.12, 0.01)
        with c_t1_3: limit_mul_t1 = st.number_input("T1 收入倍数", 1, 50, 12, help="额度锚定点")
        with c_t1_4: decay_t1 = st.slider("T1 多头衰减因子", 0.1, 1.0, 0.9, 0.05, help="越小衰减越快")

    with tabs[1]: # T2 Tab
        c_t2_1, c_t2_2, c_t2_3, c_t2_4 = st.columns(4)
        with c_t2_1: max_orgs_t2 = st.number_input("T2 最大机构数", 0, 20, 3)
        with c_t2_2: base_rate_t2 = st.number_input("T2 基准利率", 0.01, 0.36, 0.18, 0.01)
        with c_t2_3: limit_mul_t2 = st.number_input("T2 收入倍数", 1, 30, 4)
        with c_t2_4: decay_t2 = st.slider("T2 多头衰减因子", 0.1, 1.0, 0.85, 0.05)

    with tabs[2]: # T3 Tab
        c_t3_1, c_t3_2, c_t3_3, c_t3_4 = st.columns(4)
        with c_t3_1: max_orgs_t3 = st.number_input("T3 最大机构数", 0, 50, 5)
        with c_t3_2: base_rate_t3 = st.number_input("T3 基准利率", 0.01, 0.36, 0.24, 0.01)
        with c_t3_3: start_limit_t3 = st.number_input("T3 起始额度", 1000, 100000, 30000, 1000)
        with c_t3_4: decay_t3 = st.slider("T3 断崖衰减因子", 0.1, 1.0, 0.60, 0.05)

    # 绘制预览图 (放在 Top Right Column)
    with top_col2:
        # 构造模拟数据
        x_range = range(0, 10)
        preview_data = []
        temp_market_cfg = {
            'MAX_ORGS_T1': 20, 'MAX_ORGS_T2': 20, 'MAX_ORGS_T3': 20, # 预览不截断
            'LIMIT_MUL_T1': limit_mul_t1, 'LIMIT_MUL_T2': limit_mul_t2,
            'DECAY_T1': decay_t1, 'DECAY_T2': decay_t2, 'DECAY_T3': decay_t3,
            'LIMIT_START_T3': start_limit_t3, 'LIMIT_FLOOR_T3': 2000,
            'BASE_RATE_T1': base_rate_t1, 'BASE_RATE_T2': base_rate_t2, 'BASE_RATE_T3': base_rate_t3,
            'PENALTY_RATE': penalty_rate
        }
        temp_market = Market(temp_market_cfg)
        
        for i in x_range:
            l1, r1 = temp_market.get_limit_and_rate('T1', income, i)
            l2, r2 = temp_market.get_limit_and_rate('T2', income, i)
            l3, r3 = temp_market.get_limit_and_rate('T3', income, i)
            preview_data.append({'Count': i, 'Limit': l1, 'Rate': r1, 'Tier': 'T1 (银行)'})
            preview_data.append({'Count': i, 'Limit': l2, 'Rate': r2, 'Tier': 'T2 (消金)'})
            preview_data.append({'Count': i, 'Limit': l3, 'Rate': r3, 'Tier': 'T3 (网贷)'})
            
        df_prev = pd.DataFrame(preview_data)
        
        # 使用 Plotly Subplots 或者两个小图
        sub_col1, sub_col2 = st.columns(2)
        with sub_col1:
            fig_limit = px.line(
                df_prev, x='Count', y='Limit', color='Tier', 
                title="授信额度衰减 (Limit Decay)", markers=False,
                color_discrete_map={'T1 (银行)': '#3498db', 'T2 (消金)': '#f39c12', 'T3 (网贷)': '#e74c3c'},
                height=250
            )
            fig_limit.update_layout(margin=dict(l=20, r=20, t=30, b=20), legend=dict(orientation="h", y=-0.2))
            st.plotly_chart(fig_limit, use_container_width=True)
            
        with sub_col2:
            fig_rate = px.line(
                df_prev, x='Count', y='Rate', color='Tier', 
                title="利率惩罚上浮 (Rate Penalty)", markers=False,
                color_discrete_map={'T1 (银行)': '#3498db', 'T2 (消金)': '#f39c12', 'T3 (网贷)': '#e74c3c'},
                height=250
            )
            fig_rate.update_yaxes(tickformat=".0%")
            fig_rate.update_layout(margin=dict(l=20, r=20, t=30, b=20), legend=dict(orientation="h", y=-0.2))
            st.plotly_chart(fig_rate, use_container_width=True)

# 组装真实 Config
real_market_config = {
    'MAX_ORGS_T1': max_orgs_t1, 'MAX_ORGS_T2': max_orgs_t2, 'MAX_ORGS_T3': max_orgs_t3,
    'LIMIT_MUL_T1': limit_mul_t1, 'LIMIT_MUL_T2': limit_mul_t2,
    'DECAY_T1': decay_t1, 'DECAY_T2': decay_t2,
    'LIMIT_START_T3': start_limit_t3, 'LIMIT_FLOOR_T3': 2000, 'DECAY_T3': decay_t3,
    'BASE_RATE_T1': base_rate_t1, 'BASE_RATE_T2': base_rate_t2, 'BASE_RATE_T3': base_rate_t3, 
    'PENALTY_RATE': penalty_rate
}

# --- Execution Section ---
if run_btn:
    st.divider()
    st.markdown("### 📊 推演结果 (Simulation Result)")
    
    loans_list = []
    for _, row in edited_df.iterrows():
        loans_list.append(Loan(
            row["名称"], row["机构代码"], row["账户类型"],
            row["额度"], row["余额"], row["月供"], row["到期月数"], row["利率"]
        ))
    
    market = Market(real_market_config)
    sim = DebtSpiralSimulator(income, savings, living_cost, loans_list, market)
    
    # Run
    for i in range(36):
        if sim.is_dead: break
        sim.run_month()
        
    # KPI Cards
    c1, c2, c3, c4 = st.columns(4)
    final_debt = sum(l.balance for l in sim.loans)
    
    with c1:
        if sim.is_dead: st.error(f"❌ 违约: Month {sim.month}")
        else: st.success("✅ 幸存: 36 Months")
    with c2: st.metric("最终负债", f"¥{final_debt:,.0f}")
    with c3: st.metric("杠杆率 (DTI)", f"{(final_debt/income):.1f} x")
    with c4: 
        cnt = sim.get_counts()
        st.metric("持牌数 (T1/T2/T3)", f"{cnt['T1']} / {cnt['T2']} / {cnt['T3']}")

    if sim.is_dead:
        st.warning(f"💡 **死因诊断**: {sim.death_reason}")

    # Results Tabs
    res_tab1, res_tab2 = st.tabs(["📈 负债与流动性趋势", "📜 详细资金流水日志"])
    
    with res_tab1:
        df_h = pd.DataFrame(sim.history)
        if not df_h.empty:
            col_chart1, col_chart2 = st.columns(2)
            
            with col_chart1:
                fig = px.area(
                    df_h, x="Month", y=["T1_Debt", "T2_Debt", "T3_Debt"],
                    color_discrete_map={"T1_Debt": "#3498db", "T2_Debt": "#f39c12", "T3_Debt": "#e74c3c"},
                    title="债务堆叠结构 (Debt Stacking)",
                    labels={"value": "负债金额", "variable": "类型"}
                )
                if sim.is_dead: fig.add_vline(x=sim.month, line_color="red", line_dash="dash")
                st.plotly_chart(fig, use_container_width=True)

            with col_chart2:
                fig2 = go.Figure()
                fig2.add_trace(go.Scatter(x=df_h['Month'], y=df_h['Savings'], name='存款余额', line=dict(color='#27ae60', width=3)))
                fig2.add_trace(go.Bar(x=df_h['Month'], y=df_h['Gap'], name='资金缺口', marker_color='#c0392b'))
                fig2.update_layout(title="流动性生存线 (Liquidity)", barmode='stack')
                st.plotly_chart(fig2, use_container_width=True)
            
    with res_tab2:
        df_l = pd.DataFrame(sim.structured_logs)
        cat_filter = st.multiselect("🔍 筛选类别", df_l["类别"].unique(), default=df_l["类别"].unique())
        
        st.dataframe(
            df_l[df_l["类别"].isin(cat_filter)].style.format({"金额变动": "{:,.2f}"})
            .map(lambda x: 'color:#e74c3c' if x<0 else 'color:#27ae60', subset=['金额变动']),
            use_container_width=True,
            height=400
        )