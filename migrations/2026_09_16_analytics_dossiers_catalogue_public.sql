-- Analytics publiques des dossiers du catalogue Clovis.
-- Les compteurs sont agrégés au niveau du dossier pour rester simples,
-- rapides à lire et indépendants de l'identité du visiteur.
alter table dossiers_catalogue_public
  add column if not exists vues bigint not null default 0,
  add column if not exists telechargements bigint not null default 0,
  add column if not exists partages bigint not null default 0;

create or replace function incrementer_analytics_dossier_catalogue_public(
  p_dossier_id uuid,
  p_evenement text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_evenement = 'vue' then
    update dossiers_catalogue_public set vues = vues + 1 where id = p_dossier_id;
  elsif p_evenement = 'telechargement' then
    update dossiers_catalogue_public set telechargements = telechargements + 1 where id = p_dossier_id;
  elsif p_evenement = 'partage' then
    update dossiers_catalogue_public set partages = partages + 1 where id = p_dossier_id;
  else
    raise exception 'ANALYTICS_EVENEMENT_INVALIDE';
  end if;
end;
$$;
