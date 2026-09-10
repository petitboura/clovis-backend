-- Refonte du système de signalements pédagogiques (10/09/2026, demande
-- Bourama) : plus de type A/B, plus de génération automatique de
-- comportement, plus de cascade de supervision. Voir core/signalements.py.
--
-- IMPORTANT : cette migration doit être rejouable sans écraser un état
-- déjà à jour (le rename échoue silencieusement si "signalements"
-- existe déjà et "corrections_pedagogiques" non -- voir garde en tête
-- de bloc).

begin;

drop table if exists cascades_supervision cascade;

do $$
begin
  if exists (select 1 from information_schema.tables where table_name = 'corrections_pedagogiques')
     and not exists (select 1 from information_schema.tables where table_name = 'signalements') then
    alter table corrections_pedagogiques rename to signalements;
  end if;
end $$;

alter table signalements
  drop column if exists type,
  drop column if exists comportement_id,
  drop column if exists statut_cascade,
  drop column if exists cascade_id;

alter table signalements
  add column if not exists probleme_observe text,
  add column if not exists visible_question boolean not null default true,
  add column if not exists visible_reponse boolean not null default true,
  add column if not exists visible_conversation boolean not null default false,
  add column if not exists demande_prof_question boolean not null default false,
  add column if not exists demande_prof_reponse boolean not null default false,
  add column if not exists demande_prof_conversation boolean not null default false,
  add column if not exists code_id uuid references codes_partage(id) on delete set null,
  add column if not exists conversation_discussion_id uuid;

alter table signalements drop constraint if exists corrections_pedagogiques_statut_check;
alter table signalements drop constraint if exists signalements_statut_check;
alter table signalements alter column statut set default 'nouveau';
update signalements set statut = 'nouveau' where statut not in ('nouveau', 'discute');
alter table signalements add constraint signalements_statut_check check (statut in ('nouveau', 'discute'));

insert into registre_outils_plateforme (nom_outil, nom_serveur, disponible, categorie)
values
  ('consulter_signalement', 'generation', true, 1),
  ('enregistrer_note_signalement', 'generation', true, 1),
  ('rattacher_signalement_notion', 'generation', true, 1)
on conflict do nothing;

commit;
