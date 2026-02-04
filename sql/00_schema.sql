-- =========================
-- TABELA: clientes
-- =========================
CREATE TABLE IF NOT EXISTS clientes (
  id_cliente BIGSERIAL PRIMARY KEY,
  nome       TEXT NOT NULL,
  email      TEXT UNIQUE,
  saldo      NUMERIC(12,2) NOT NULL DEFAULT 0,
  criado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE clientes IS 'Customer master data and available balance.';
COMMENT ON COLUMN clientes.saldo IS 'Available balance for purchases (currency).';

-- =========================
-- TABELA: produtos
-- =========================
CREATE TABLE IF NOT EXISTS produtos (
  id_produto BIGSERIAL PRIMARY KEY,
  nome       TEXT NOT NULL UNIQUE,
  preco      NUMERIC(12,2) NOT NULL CHECK (preco >= 0),
  estoque    INT NOT NULL DEFAULT 0 CHECK (estoque >= 0),
  ativo      BOOLEAN NOT NULL DEFAULT TRUE,
  criado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE produtos IS 'Product catalog with price, stock and active flag.';
COMMENT ON COLUMN produtos.preco IS 'Current product price (currency).';

-- =========================
-- TABELA: transacoes
-- =========================
CREATE TABLE IF NOT EXISTS transacoes (
  id_transacao   BIGSERIAL PRIMARY KEY,
  id_cliente     BIGINT NOT NULL REFERENCES clientes(id_cliente) ON DELETE CASCADE,
  id_produto     BIGINT NOT NULL REFERENCES produtos(id_produto),
  quantidade     INT NOT NULL DEFAULT 1 CHECK (quantidade > 0),
  preco_unitario NUMERIC(12,2) NOT NULL CHECK (preco_unitario >= 0),
  valor_total    NUMERIC(12,2) GENERATED ALWAYS AS (quantidade * preco_unitario) STORED,
  data_transacao TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE transacoes IS 'Transactions (purchases) linking customers to a single product.';
COMMENT ON COLUMN transacoes.preco_unitario IS 'Unit price at purchase time (snapshot).';
COMMENT ON COLUMN transacoes.valor_total IS 'Computed: quantidade * preco_unitario.';

CREATE INDEX IF NOT EXISTS idx_transacoes_cliente ON transacoes(id_cliente);
CREATE INDEX IF NOT EXISTS idx_transacoes_produto ON transacoes(id_produto);

INSERT INTO clientes (nome, email, saldo) VALUES
('Ana',   'ana@email.com',   3500.00),
('Bruno', 'bruno@email.com', 1200.00),
('Carla', 'carla@email.com', 8000.00),
('Diego', 'diego@email.com', 900.00);

INSERT INTO produtos (nome, preco, estoque) VALUES
('Notebook',   4500.00, 10),
('Smartphone', 2500.00, 25),
('Mouse',        120.00, 50);

INSERT INTO transacoes (id_cliente, id_produto, quantidade, preco_unitario)
SELECT c.id_cliente, p.id_produto, 1, p.preco
FROM clientes c
JOIN produtos p ON p.nome = 'Mouse'
WHERE c.nome IN ('Ana','Bruno');

INSERT INTO transacoes (id_cliente, id_produto, quantidade, preco_unitario)
SELECT c.id_cliente, p.id_produto, 1, p.preco
FROM clientes c
JOIN produtos p ON p.nome = 'Notebook'
WHERE c.nome IN ('Carla');

INSERT INTO transacoes (id_cliente, id_produto, quantidade, preco_unitario)
SELECT c.id_cliente, p.id_produto, 2, p.preco
FROM clientes c
JOIN produtos p ON p.nome = 'Smartphone'
WHERE c.nome IN ('Ana');
