-- ============================================================
-- E-WALLET PAYMENT SUCCESS RATE (SR) ANALYSIS
-- ============================================================
-- Schema assumptions:
--   transactions(transaction_id, customer_id, transaction_date,
--                product_group, category, payment_method, status, amount)
--     status = 'Success' or a failure reason, e.g.
--     '3DS Authentication Failed', 'Wrong OTP', 'System Error', 'Insufficient Balance'
--   funnel_events(customer_id, transaction_id, funnel_step, step_order, event_date)
-- ============================================================


-- 1. Monthly Success Rate by Product Group, with month-over-month change
WITH monthly_sr AS (
    SELECT
        product_group,
        DATEPART(MONTH, transaction_date) AS txn_month,
        COUNT(*) AS total_orders,
        SUM(CASE WHEN status = 'Success' THEN 1 ELSE 0 END) AS success_orders,
        CAST(SUM(CASE WHEN status = 'Success' THEN 1 ELSE 0 END) AS FLOAT)
            / COUNT(*) AS success_rate
    FROM transactions
    GROUP BY product_group, DATEPART(MONTH, transaction_date)
)
SELECT
    product_group,
    txn_month,
    total_orders,
    FORMAT(success_rate, 'p') AS success_rate,
    FORMAT(
        success_rate - LAG(success_rate) OVER (PARTITION BY product_group ORDER BY txn_month),
        'p'
    ) AS mom_change
FROM monthly_sr
ORDER BY product_group, txn_month;


-- 2. Root Cause: failure-reason breakdown, % of all failed transactions
WITH failed_txns AS (
    SELECT status AS failure_reason, COUNT(*) AS failure_count
    FROM transactions
    WHERE status <> 'Success'
    GROUP BY status
),
total_failed AS (
    SELECT SUM(failure_count) AS total FROM failed_txns
)
SELECT
    f.failure_reason,
    f.failure_count,
    FORMAT(CAST(f.failure_count AS FLOAT) / t.total, 'p') AS pct_of_failures
FROM failed_txns f
CROSS JOIN total_failed t
ORDER BY f.failure_count DESC;


-- 3. Authentication failures (3DS / OTP) isolated — the primary SR decline driver
SELECT
    product_group,
    COUNT(*) AS auth_failures,
    CAST(SUM(amount) AS DECIMAL(18,2)) AS revenue_at_risk
FROM transactions
WHERE status IN ('3DS Authentication Failed', 'Wrong OTP')
GROUP BY product_group
ORDER BY revenue_at_risk DESC;


-- 4. Revenue Pareto — cumulative % of revenue by category (80/20 check)
WITH category_revenue AS (
    SELECT category, SUM(amount) AS revenue
    FROM transactions
    WHERE status = 'Success'
    GROUP BY category
)
SELECT
    category,
    revenue,
    SUM(revenue) OVER (ORDER BY revenue DESC) AS running_total,
    FORMAT(
        SUM(revenue) OVER (ORDER BY revenue DESC) / SUM(revenue) OVER (),
        'p'
    ) AS cumulative_pct
FROM category_revenue
ORDER BY revenue DESC;


-- 5. Funnel step drop-off — conversion rate from each step to the next
WITH step_counts AS (
    SELECT
        funnel_step,
        step_order,
        COUNT(DISTINCT transaction_id) AS users_at_step
    FROM funnel_events
    GROUP BY funnel_step, step_order
)
SELECT
    funnel_step,
    users_at_step,
    LAG(users_at_step) OVER (ORDER BY step_order) AS prev_step_users,
    FORMAT(
        CAST(users_at_step AS FLOAT)
            / NULLIF(LAG(users_at_step) OVER (ORDER BY step_order), 0),
        'p'
    ) AS conversion_from_prev_step
FROM step_counts
ORDER BY step_order;
