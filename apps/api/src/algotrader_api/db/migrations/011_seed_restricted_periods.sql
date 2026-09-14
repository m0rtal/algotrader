-- 011_seed_restricted_periods.sql
-- Seeds the 2022 MOEX trading restriction period. On 2022-02-24
-- MOEX suspended all trading in response to Russia's invasion of
-- Ukraine. Trading resumed on 2022-03-21 for select Russian
-- residents only; foreigners remained barred from selling Russian
-- equities until 2022-04-01 (Federal Law 114-FZ).
--
-- Source: MOEX press release 2022-02-24 / Federal Law 46-FZ /
-- Federal Law 114-FZ. Cross-checked with daily MOEX session reports.

INSERT OR IGNORE INTO restricted_periods (date, reason) VALUES
    ('2022-02-24', 'MOEX full session halt in response to invasion of Ukraine'),
    ('2022-02-25', 'MOEX full session halt'),
    ('2022-02-28', 'MOEX full session halt'),
    ('2022-03-01', 'MOEX full session halt'),
    ('2022-03-02', 'MOEX full session halt'),
    ('2022-03-03', 'MOEX full session halt'),
    ('2022-03-04', 'MOEX full session halt'),
    ('2022-03-07', 'MOEX full session halt'),
    ('2022-03-09', 'MOEX full session halt'),
    ('2022-03-10', 'MOEX full session halt'),
    ('2022-03-11', 'MOEX full session halt'),
    ('2022-03-14', 'MOEX full session halt'),
    ('2022-03-15', 'MOEX full session halt'),
    ('2022-03-16', 'MOEX full session halt'),
    ('2022-03-17', 'MOEX full session halt'),
    ('2022-03-18', 'MOEX full session halt'),
    ('2022-03-21', 'Restricted trading: Russian residents only'),
    ('2022-03-22', 'Restricted trading: Russian residents only'),
    ('2022-03-23', 'Restricted trading: Russian residents only'),
    ('2022-03-24', 'Restricted trading: Russian residents only'),
    ('2022-03-25', 'Restricted trading: Russian residents only'),
    ('2022-03-28', 'Restricted trading: Russian residents only'),
    ('2022-03-29', 'Restricted trading: Russian residents only'),
    ('2022-03-30', 'Restricted trading: Russian residents only'),
    ('2022-03-31', 'Restricted trading: Russian residents only');
