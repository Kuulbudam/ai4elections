-- Phase 11: answers in Hausa, Yoruba, Igbo or Nigerian Pidgin. The log keeps which language was asked for.
ALTER TABLE public.assistant_queries
  ADD COLUMN language text NOT NULL DEFAULT 'en' CHECK (language IN ('en', 'ha', 'yo', 'ig', 'pcm'));
