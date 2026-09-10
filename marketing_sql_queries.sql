-- ============================================================
-- MARKETING CAMPAIGN DASHBOARD (Facebook / Instagram / Pinterest)
-- ============================================================
-- Schema assumption:
--   campaign_performance(campaign_id, channel, report_date,
--                         spend, impressions, clicks, conversions, revenue)
-- ============================================================


-- 1. Channel-level performance summary: CTR, CPA, ROAS
SELECT
    channel,
    SUM(spend) AS total_spend,
    SUM(clicks) AS total_clicks,
    SUM(conversions) AS total_conversions,
    SUM(revenue) AS total_revenue,
    FORMAT(CAST(SUM(clicks) AS FLOAT) / NULLIF(SUM(impressions), 0), 'p') AS ctr,
    CAST(SUM(spend) / NULLIF(SUM(conversions), 0) AS DECIMAL(18,2)) AS cpa,
    CAST(SUM(revenue) / NULLIF(SUM(spend), 0) AS DECIMAL(18,2)) AS roas
FROM campaign_performance
GROUP BY channel
ORDER BY roas DESC;


-- 2. BCG Matrix input: spend share (x-axis) vs ROAS (y-axis) per channel
WITH channel_totals AS (
    SELECT
        channel,
        SUM(spend) AS channel_spend,
        SUM(revenue) AS channel_revenue
    FROM campaign_performance
    GROUP BY channel
),
grand_total AS (
    SELECT SUM(channel_spend) AS total_spend FROM channel_totals
)
SELECT
    c.channel,
    FORMAT(c.channel_spend / g.total_spend, 'p') AS spend_share,
    CAST(c.channel_revenue / NULLIF(c.channel_spend, 0) AS DECIMAL(18,2)) AS roas
FROM channel_totals c
CROSS JOIN grand_total g
ORDER BY roas DESC;


-- 3. Month-over-month spend & revenue trend, with growth % via LAG()
WITH monthly_perf AS (
    SELECT
        channel,
        DATEPART(YEAR, report_date) AS yr,
        DATEPART(MONTH, report_date) AS mo,
        SUM(spend) AS monthly_spend,
        SUM(revenue) AS monthly_revenue
    FROM campaign_performance
    GROUP BY channel, DATEPART(YEAR, report_date), DATEPART(MONTH, report_date)
)
SELECT
    channel,
    yr,
    mo,
    monthly_spend,
    monthly_revenue,
    FORMAT(
        (monthly_revenue - LAG(monthly_revenue) OVER (PARTITION BY channel ORDER BY yr, mo))
            / NULLIF(LAG(monthly_revenue) OVER (PARTITION BY channel ORDER BY yr, mo), 0),
        'p'
    ) AS revenue_mom_growth
FROM monthly_perf
ORDER BY channel, yr, mo;


-- 4. Budget reallocation check: Facebook (Cash Cow, harvest candidate)
--    vs Pinterest (Question Mark -> Star), ranked by ROAS
WITH channel_summary AS (
    SELECT
        channel,
        SUM(spend) AS total_spend,
        SUM(revenue) AS total_revenue,
        CAST(SUM(revenue) / NULLIF(SUM(spend), 0) AS DECIMAL(18,2)) AS roas
    FROM campaign_performance
    WHERE channel IN ('Facebook', 'Pinterest')
    GROUP BY channel
)
SELECT
    channel,
    total_spend,
    total_revenue,
    roas,
    RANK() OVER (ORDER BY roas DESC) AS roas_rank
FROM channel_summary;
