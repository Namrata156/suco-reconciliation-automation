-- ============================================================================
-- docs/phase2_rule_table_v3_1_reference.sql
-- Acceptance oracle for the Phase 2 Step 4 rule evaluator.
-- Encodes UPI business rule table v3.1 (docs/phase2_rule_table_v3_approved.txt)
-- as one CASE expression, first match wins, in the doc's evaluation order.
-- Read-only: run against the rebuilt presence matrix. On 2026-10-09 it gives
-- totals 77354 / 79096 / 76631 for 14 Jul / 25 Sep / 26 Sep 2026; per-rule
-- counts are in rule_engine/seed_data/v3_1.py EXPECTED_COUNTS. The seeded
-- rule conditions (rule_engine, rule set v3.1) were checked against these
-- CASE branches rule by rule. Copied unchanged below this header.
-- ============================================================================
-- Reference oracle for rule table v3.1 (first match wins). Read-only.
with s as (select id, txn_date_raw from transactions_stagingadjustment
           union all select original_id, txn_date_raw from transactions_stagingadjustmenthistory),
g as (
  select g.id, g.business_date, g.is_on_us, g.cbs_reversed, g.amount_match, g.direction_match,
    c.present as cbs, c.paired_transaction_record_id as cbs_pair,
    n.present as npci, n.response_code as code,
    d.ssk_side as drc_ssk, t.ssk_side as tmo_ssk, a.ssk_side as adj_ssk, a.dispute_type as adj_type,
    af.business_date - to_date(s.txn_date_raw,'DD-MM-YYYY') as adj_age_days
  from reconciliation_transactionmatchgroup g
  join reconciliation_registerpresence c on c.match_group_id=g.id and c.leg='CBS'
  join reconciliation_registerpresence n on n.match_group_id=g.id and n.leg='NPCI'
  left join reconciliation_registerpresence d on d.match_group_id=g.id and d.leg='DRC'
  left join reconciliation_registerpresence t on t.match_group_id=g.id and t.leg='TIMEOUT'
  left join reconciliation_registerpresence a on a.match_group_id=g.id and a.leg='ADJUSTMENT'
  left join transactions_transactionfact af on af.id=a.transaction_record_id
  left join s on s.id=af.staging_row_id
  where not g.is_superseded),
r as (select business_date, case
  when drc_ssk and cbs and cbs_reversed then 'R01'
  when drc_ssk and cbs then 'R02'
  when drc_ssk then 'R03'
  when tmo_ssk and cbs and not cbs_reversed then 'R04a'
  when tmo_ssk and cbs then 'R04b'
  when tmo_ssk then 'R05'
  when adj_ssk and adj_type in ('Credit Adjustment','Online Refund','RET') and cbs and cbs_reversed then 'R06b'
  when adj_ssk and adj_type in ('Credit Adjustment','Online Refund','RET') then 'R06'
  when adj_ssk and adj_type in ('Debit Reversal Confirmation','Refund Reversal Confirmation','TCC') then 'R07a'
  when adj_ssk and (adj_type ilike '%chargeback%' or adj_type ilike '%presentment%') and adj_age_days > 35 then 'R07x'
  when adj_ssk and (adj_type ilike '%chargeback%' or adj_type ilike '%presentment%') then 'R07b'
  when adj_ssk then 'R07c'
  when tmo_ssk = false then 'R04c'
  when (drc_ssk = false or adj_ssk = false) and not cbs and not npci then 'R08'
  when cbs and npci and amount_match and direction_match and code='00' and not cbs_reversed
       and not (is_on_us and cbs_pair is null) then 'R09'
  when cbs_reversed then 'R10'
  when npci and not cbs and code<>'00' then 'R11'
  when npci and not cbs then 'R12'
  when cbs and not npci then 'R13'
  when is_on_us and cbs and cbs_pair is null then 'R14'
  when cbs and npci and amount_match = false then 'R15'
  when cbs and npci and direction_match = false then 'R16'
  when cbs and npci and code<>'00' then 'R17'
  else 'R99' end rule from g)
select rule,
  count(*) filter (where business_date='2026-07-14') jul14,
  count(*) filter (where business_date='2026-09-25') sep25,
  count(*) filter (where business_date='2026-09-26') sep26
from r group by rollup(rule) order by rule nulls last;
