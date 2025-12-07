import streamlit as st
import pandas as pd
import altair as alt

# ==========================================
# 0. 页面基础配置
# ==========================================
st.set_page_config(page_title="债务螺旋模型", layout="wide")

st.title("🌪️ 个人债务螺旋死亡模型")
st.markdown("""
**核心逻辑**：
在“资产荒”与“流动性泛滥”并存的宏观金融环境下，传统的信贷违约逻辑正在发生变化。
金融机构为追求收益，倾向于过度授信，使得借款人能够通过“以贷养贷”维持表面的偿债能力。
本模型旨在打破“资不抵债即违约”的传统认知，构建一个基于现金流演化 的动态系统，
预测个人在极端债务压力下的生存极限。
""")

# ==========================================
# 1. 侧边栏：参数配置
# ==========================================

with st.sidebar:
    st.header("1. 客户基础画像 (Basic Profile)")
    income = st.number_input("月收入 (Monthly Income)", value=6000, step=1000)
    initial_savings = st.number_input("初始存款 (Initial Savings)", value=10000, step=10000, help="如果负债超限，存款是唯一续命手段")
    base_living_cost = st.number_input("刚性生活费 (Living Cost)", value=5000, step=500)
    mortgage = st.number_input("房贷/固定支出 (Fixed Mortgage)", value=5000, step=500)

    st.markdown("---")
    st.header("2. 金融市场参数 (Market Rules)")
    
    # --- Tier 1 ---
    st.subheader("🏦 Tier 1 (银行/优质)")
    col_t1_1, col_t1_2 = st.columns(2)
    t1_max_orgs = col_t1_1.number_input("T1 资方上限数量", value=2, min_value=1)
    t1_limit_mult = col_t1_2.slider("T1 单机构倍数", 3, 24, 12)
    t1_apr = st.slider("Tier 1 年化利率 (%)", 3.0, 12.0, 8.0) / 100
    
    # --- Tier 2 ---
    st.subheader("💳 Tier 2 (借呗等/消金/次级)")
    col_t2_1, col_t2_2 = st.columns(2)
    t2_max_orgs = col_t2_1.number_input("T2 资方上限数量", value=3, min_value=1)
    t2_limit_mult = col_t2_2.slider("T2 单机构倍数", 6, 24, 12)
    t2_apr = st.slider("Tier 2 年化利率 (%)", 10.0, 24.0, 18.0) / 100
    
    # --- Tier 3 ---
    st.subheader("🦈 Tier 3 (网贷/高危)")
    t3_enable = st.checkbox("启用高利贷/Tier 3?", value=True)
    if t3_enable:
        col_t3_1, col_t3_2 = st.columns(2)
        t3_max_orgs = col_t3_1.number_input("T3 资方上限数量", value=5, min_value=1)
        t3_limit_fixed = col_t3_2.number_input("T3 单机构额度", value=20000, step=5000)
    else:
        t3_max_orgs = 0
        t3_limit_fixed = 0
    # t3_apr = 0.24 # 锁定 24%
    t3_apr = st.slider("Tier 3 年化利率 (%)", 24.0, 100.0, 36.0) / 100

    st.markdown("---")
    st.header("3. 客户存量债务 (Existing Debt)")
    
    # --- Tier 1 存量 ---
    with st.expander("Tier 1 债务详情", expanded=True):
        col1, col2 = st.columns(2)
        # 允许用户输入任意金额，即使超过 limit
        t1_debt_init = col1.number_input("T1 当前余额", value=300000, step=10000)
        t1_due_month = col2.slider("T1 本金到期月份", 1, 12, 3)

    # --- Tier 2 存量 ---
    with st.expander("Tier 2 债务详情", expanded=False):
        col1, col2 = st.columns(2)
        t2_debt_init = col1.number_input("T2 当前余额", value=200000, step=10000)
        t2_due_month = col2.slider("T2 本金到期月份", 1, 12, 3)

    # --- Tier 3 存量 ---
    with st.expander("Tier 3 债务详情", expanded=False):
        if t3_enable:
            col1, col2 = st.columns(2)
            t3_debt_init = col1.number_input("T3 当前余额", value=0, step=5000)
            t3_due_month = col2.slider("T3 本金到期月份", 1, 24, 3)
        else:
            t3_debt_init = 0
            t3_due_month = 999

# ==========================================
# 2. 模拟逻辑引擎 (Core Logic)
# ==========================================

def run_simulation():
    months = 24
    
    # --- 1. 计算各层级总额度上限 (Caps) ---
    limit_t1_total = min(200000,income * t1_limit_mult) * t1_max_orgs
    limit_t2_total = min(200000,income * t2_limit_mult) * t2_max_orgs
    limit_t3_total = t3_limit_fixed * t3_max_orgs
    
    # total_market_capacity = limit_t1_total + limit_t2_total + limit_t3_total
    
    # --- 2. 初始负债设定 ---
    debt_t1 = t1_debt_init
    debt_t2 = t2_debt_init
    debt_t3 = t3_debt_init
    savings = initial_savings
    
    # 移除开局阻断！即使 raw_total_debt > total_market_capacity，也可以开始。
    # status 默认为 Safe
    status = "Safe"
    fail_month = None

    history = []

    for t in range(1, months + 1):
        if "Default" in status:
            break
            
        # --- A. 费用产生 (Outflows) ---
        
        # 1. 利息计算
        int_t1 = debt_t1 * (t1_apr / 12)
        int_t2 = debt_t2 * (t2_apr / 12)
        int_t3 = debt_t3 * (t3_apr / 12)
        total_interest = int_t1 + int_t2 + int_t3
        
        # 2. 本金到期检测 (Rollover)
        pay_back_t1 = 0
        pay_back_t2 = 0
        pay_back_t3 = 0
        
        if t == t1_due_month: pay_back_t1 = debt_t1
        if t == t2_due_month: pay_back_t2 = debt_t2
        if t == t3_due_month: pay_back_t3 = debt_t3
        
        total_principal_due = pay_back_t1 + pay_back_t2 + pay_back_t3
        
        # 3. 总刚性支出
        living_fixed = base_living_cost + mortgage
        total_cash_needed = living_fixed + total_interest + total_principal_due
        
        # --- B. 资金结算 ---
        net_flow = income - total_cash_needed
        
        # --- C. 缺口填补与续贷 (Gap Filling) ---
        
        # 模拟还款后的临时债务状态 (这决定了能借出多少钱)
        temp_debt_t1 = debt_t1 - pay_back_t1
        temp_debt_t2 = debt_t2 - pay_back_t2
        temp_debt_t3 = debt_t3 - pay_back_t3
        
        gap = abs(net_flow) if net_flow < 0 else 0
        
        if net_flow > 0:
            savings += net_flow
        else:
            # 1. 吃存款 (唯一的救命稻草，如果额度已经超限)
            if savings >= gap:
                savings -= gap
                gap = 0
            else:
                gap -= savings
                savings = 0
                
            # 2. 借贷填坑 (Borrowing Logic)
            # 逻辑：只有当 (总上限 - 当前负债) > 0 时，才能借出新钱。
            # 如果初始状态就是超限的，这里 calculated availability 会是负数，draw 就会是 0。
            # 这意味着客户在超限期间，无法借款付利息，只能死扛。
            
            # T1
            avail_t1 = limit_t1_total - temp_debt_t1
            if gap > 0 and avail_t1 > 0:
                draw = min(avail_t1, gap)
                temp_debt_t1 += draw
                gap -= draw
            
            # T2
            avail_t2 = limit_t2_total - temp_debt_t2
            if gap > 0 and avail_t2 > 0:
                draw = min(avail_t2, gap)
                temp_debt_t2 += draw
                gap -= draw
                    
            # T3
            avail_t3 = limit_t3_total - temp_debt_t3
            if gap > 0 and t3_enable and avail_t3 > 0:
                draw = min(avail_t3, gap)
                temp_debt_t3 += draw
                gap -= draw
            
            # 3. 违约判定
            if gap > 1:
                status = "Default"
                fail_month = t
        
        # 更新
        debt_t1 = temp_debt_t1
        debt_t2 = temp_debt_t2
        debt_t3 = temp_debt_t3
        
        # 记录
        total_debt = debt_t1 + debt_t2 + debt_t3
        total_limit = limit_t1_total + limit_t2_total + limit_t3_total
        
        def estimate_orgs(debt, limit_per_org, max_orgs):
            if limit_per_org == 0: return 0
            usage = debt / limit_per_org
            return min(max_orgs, float(usage))

        history.append({
            "Month": t,
            "Total_Debt": round(total_debt, 2),
            "Debt_Tier1": round(debt_t1, 2),
            "Debt_Tier2": round(debt_t2, 2),
            "Debt_Tier3": round(debt_t3, 2),
            "Monthly_Interest": round(total_interest, 2),
            "Rollover_Event": total_principal_due > 0,
            "Savings": round(savings, 2),
            "Limit_Total": round(total_limit, 2),
            "Orgs_Used_T1": estimate_orgs(debt_t1, income * t1_limit_mult, t1_max_orgs),
            "Orgs_Used_T3": estimate_orgs(debt_t3, t3_limit_fixed, t3_max_orgs),
            "Status": status
        })

    return pd.DataFrame(history), fail_month, (limit_t1_total, limit_t2_total, limit_t3_total)

# ==========================================
# 3. 运行与可视化
# ==========================================

if st.sidebar.button('▶️ 开始推演 (Run Simulation)', type="primary"):
    df, fail_month, limits = run_simulation()
    
    # 预防极端情况
    if df.empty:
        st.error("未知错误：模拟未生成数据")
        st.stop()
        
    last_rec = df.iloc[-1]
    final_debt = last_rec['Total_Debt']
    total_limit_all = sum(limits)
    
    # 结果摘要
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if fail_month:
            st.error(f"❌ 违约: 第 {fail_month} 月")
        else:
            st.success("✅ 存活: 24个月")
    with col2:
        delta = final_debt - (t1_debt_init+t2_debt_init+t3_debt_init)
        st.metric("最终总负债", f"¥{final_debt:,.0f}", delta=f"{delta:,.0f}")
    with col3:
        st.metric("Tier 3 机构使用数", f"{last_rec.get('Orgs_Used_T3', 0):.1f} / {t3_max_orgs} 家")
    with col4:
        # 如果负债超限，使用率会超过 100%
        rate = (final_debt / total_limit_all) * 100 if total_limit_all > 0 else 0
        st.metric("额度耗尽率", f"{rate:.1f}%", delta_color="inverse" if rate > 100 else "normal")

    # 特殊提示：如果当前超限
    if final_debt > total_limit_all:
        st.warning(f"⚠️ **严重警报**：当前负债 (¥{final_debt:,.0f}) 已超过市场承载上限 (¥{total_limit_all:,.0f})。客户处于“僵尸状态”，完全依赖存款支付利息，一旦存款耗尽或本金到期将立即违约。")

    # 图表1：债务构成
    st.subheader("1. 债务结构演化 (Debt Composition)")
    debt_melt = df.melt('Month', value_vars=['Debt_Tier3', 'Debt_Tier2', 'Debt_Tier1'], var_name='Type', value_name='Amount')
    c1 = alt.Chart(debt_melt).mark_area().encode(
        x='Month:O',
        y='Amount:Q',
        color=alt.Color('Type', scale=alt.Scale(domain=['Debt_Tier3', 'Debt_Tier2', 'Debt_Tier1'], range=['#ff6384', '#ffce56', '#36a2eb']))
    ).properties(height=300)
    st.altair_chart(c1, use_container_width=True)
    
    # 图表2：生死线
    st.subheader("2. 市场容量监测 (Market Capacity)")
    base = alt.Chart(df).encode(x='Month:O')
    line_debt = base.mark_line(color='red', strokeWidth=3).encode(y='Total_Debt')
    line_limit = base.mark_line(color='green', strokeDash=[5,5]).encode(y='Limit_Total')
    points = base.mark_circle(color='orange', size=80).encode(
        y='Total_Debt', opacity=alt.condition(alt.datum.Rollover_Event, alt.value(1), alt.value(0)),
        tooltip="Rollover_Event"
    )
    st.altair_chart((line_debt + line_limit + points).interactive(), use_container_width=True)
    st.caption("🔴 红线：总负债 (若高于绿线，说明处于“僵尸”状态) | 🟢 绿虚线：市场总资金上限 | 🟠 借新还旧时刻")
    
    # 数据表
    with st.expander("查看详细数据"):
        st.dataframe(df)

else:
    st.info("👈 请调整左侧参数，点击上方按钮开始推演。")