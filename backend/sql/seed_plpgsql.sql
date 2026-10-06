-- PostgreSQL / PL/pgSQL helpers for RYZE (optional; Python seed is the default).
-- Run against DATABASE_URL=postgresql://ryze:ryze@localhost:5432/ryze_bi after tables exist.

CREATE OR REPLACE FUNCTION ryze_live_tick(p_domain text)
RETURNS numeric AS $$
DECLARE v_amount numeric;
BEGIN
  v_amount := round((random() * 900 + 40)::numeric, 2);
  INSERT INTO live_events(domain, event_type, amount, created_at)
  VALUES (p_domain, 'plsql_tick', v_amount, NOW());
  RETURN v_amount;
END;
$$ LANGUAGE plpgsql;
