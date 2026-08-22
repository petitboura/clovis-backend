-- Refonte "vraiment comme Notion" (Partie 1/2), 2026-08-21, demande
-- Bourama. Icône emoji de page (affichée dans la sidebar et en tête du
-- canevas), comme sur Notion. Nullable -- une page sans icône garde
-- l'icône par défaut (FileText) côté frontend.

alter table pages add column if not exists icone text;
