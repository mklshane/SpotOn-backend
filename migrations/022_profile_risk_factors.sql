-- 022: optional skin-cancer risk factors used to personalise result advice ("For you" notes).
-- All nullable: null = not answered. Sensitive health data - the API only stores them when
-- consent_data_privacy is true, like fitzpatrick_skin_type. They never change the triage tier.
-- Apply before deploying API code that reads these columns.

begin;

alter table public.users add column if not exists personal_skin_cancer_history text
  check (personal_skin_cancer_history in ('yes', 'no', 'unsure'));
alter table public.users add column if not exists family_skin_cancer_history text
  check (family_skin_cancer_history in ('yes', 'no', 'unsure'));
alter table public.users add column if not exists high_sun_exposure boolean;

commit;
