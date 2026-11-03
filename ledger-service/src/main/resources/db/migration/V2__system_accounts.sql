-- System funding accounts. Deposits debit these, so they are the only accounts allowed to go negative.
-- One per supported currency; an account's currency is supported iff a system account exists for it.
insert into accounts (id, owner_name, currency, status, is_system)
values ('00000000-0000-0000-0000-000000000001', 'SYSTEM FUNDING INR', 'INR', 'ACTIVE', true),
       ('00000000-0000-0000-0000-000000000002', 'SYSTEM FUNDING USD', 'USD', 'ACTIVE', true);

insert into account_balances (account_id, balance_minor)
values ('00000000-0000-0000-0000-000000000001', 0),
       ('00000000-0000-0000-0000-000000000002', 0);
