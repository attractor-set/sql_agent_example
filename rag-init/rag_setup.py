import os
import hashlib
import psycopg
from typing import List, Set
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_core.documents import Document

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception as _:
    pass


DB_DSN = os.getenv("DB_DSN")
CONNECTION = os.getenv("CONNECTION")
COLLECTION = os.getenv("COLLECTION", "kb_sql_schema")
RAG_RESET = os.getenv("RAG_RESET", "false").lower() == "true"
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")

emb = OpenAIEmbeddings(model=EMBED_MODEL)

vectorstore = PGVector(
    connection=CONNECTION,
    embeddings=emb,
    collection_name=COLLECTION,
    use_jsonb=True,
    pre_delete_collection=RAG_RESET
)

kb_docs = [Document(page_content=("TABLE clientes:\n"
                                  "- id_cliente BIGSERIAL PK\n"
                                  "- nome TEXT NOT NULL\n"
                                  "- email TEXT UNIQUE (optional)\n"
                                  "- saldo NUMERIC(12,2) NOT NULL DEFAULT 0\n"
                                  "- criado_em TIMESTAMPTZ DEFAULT now()\n"
                                  "Meaning: saldo is available balance."),
                    metadata={"type": "ddl", "table": "clientes"}),

           Document(page_content=("TABLE produtos:\n"
                                  "- id_produto BIGSERIAL PK\n"
                                  "- nome TEXT UNIQUE NOT NULL\n"
                                  "- preco NUMERIC(12,2) CHECK >= 0\n"
                                  "- estoque INT CHECK >= 0\n"
                                  "- ativo BOOLEAN DEFAULT TRUE\n"
                                  "- criado_em TIMESTAMPTZ DEFAULT now()\n"
                                  "Meaning: preco is CURRENT price."),
                    metadata={"type": "ddl", "table": "produtos"}),

           Document(page_content=("TABLE transacoes:\n"
                                  "- id_transacao BIGSERIAL PK\n"
                                  "- id_cliente BIGINT FK -> clientes(id_cliente)\n"
                                  "- id_produto BIGINT FK -> produtos(id_produto)\n"
                                  "- quantidade INT > 0\n"
                                  "- preco_unitario NUMERIC(12,2) snapshot at purchase time\n"
                                  "- valor_total = quantidade * preco_unitario (generated)\n"
                                  "- data_transacao TIMESTAMPTZ DEFAULT now()\n\n"
                                  "IMPORTANT: For historical totals use transacoes.valor_total or preco_unitario, "
                                  "NOT produtos.preco."),
                    metadata={"type": "ddl", "table": "transacoes"}),

           Document(page_content=("RELATIONSHIPS / JOINS:\n"
                                  "- clientes 1:N transacoes via transacoes.id_cliente = clientes.id_cliente\n"
                                  "- produtos 1:N transacoes via transacoes.id_produto = produtos.id_produto\n"
                                  "Product names like 'Notebook' and 'Smartphone' are stored in produtos.nome."),
                    metadata={"type": "rules"}),

           Document(page_content=("DEFAULTS:\n"
                                  "- If no time period is specified, assume full available history (no date filter).\n\n"
                                  "SQL GUARDRAILS:\n"
                                  "- Do not invent columns or tables.\n"
                                  "- Use explicit JOINs on FK columns.\n"
                                  "- Avoid SELECT *.\n"
                                  "- Aggregations: use SUM(transacoes.valor_total).\n"
                                  "- Only ask for clarification if still ambiguous after applying defaults."),
                    metadata={"type": "rules"}),
    
           Document(page_content=("EXAMPLE SQL: CLient who bought Notebook\n"
                                  "SELECT DISTINCT c.id_cliente, c.nome\n"
                                  "FROM clientes c\n"
                                  "JOIN transacoes t ON t.id_cliente = c.id_cliente\n"
                                  "JOIN produtos p ON p.id_produto = t.id_produto\n"
                                  "WHERE p.nome = 'Notebook';"),
                    metadata={"type": "example_sql"}),

           Document(page_content=("JOIN_CARD: clientes_to_transacoes\n"
                                  "JOIN_CARD_TEXT: JOIN clientes c JOIN transacoes t ON t.id_cliente = c.id_cliente (1-to-many)\n"
                                  "SEMANTICS:\n"
                                  "- Use when you need customer attributes (c.*) and purchase facts (t.*).\n"
                                  "- Cardinality: one customer can have many transactions.\n"
                                  "- Preferred join type: INNER for 'client who bought', LEFT when keeping all client.\n"),
                    metadata={"type": "join_card",
                              "card_id": "clientes_to_transacoes",
                              "left_table": "clientes",
                              "right_table": "transacoes",
                              "relationship": "one_to_many"}),
           
           Document(page_content=("JOIN_CARD: produtos_to_transacoes\n"
                                  "JOIN_CARD_TEXT: JOIN produtos p JOIN transacoes t ON t.id_produto = p.id_produto (1-to-many)\n"
                                  "SEMANTICS:\n"
                                  "- Use when you need product attributes (p.*) and purchase facts (t.*).\n"
                                  "- Cardinality: one product can appear in many transactions.\n"
                                  "- Preferred join type: INNER for 'who bought product', LEFT when keeping all products.\n"),
                    metadata={"type": "join_card",
                              "card_id": "produtos_to_transacoes",
                              "left_table": "produtos",
                              "right_table": "transacoes",
                              "relationship": "one_to_many"}),

           Document(page_content=("JOIN_CARD: clientes_transacoes_produtos_bridge\n"
                                  "JOIN_CARD_TEXT: JOIN clientes c "
                                  "JOIN transacoes t ON t.id_cliente = c.id_cliente "
                                  "JOIN produtos p ON p.id_produto = t.id_produto (bridge via transacoes)\n"
                                  "SEMANTICS:\n"
                                  "- Use for questions like 'which client bought <product>' or 'sales by product and customer'.\n"
                                  "- Apply product name filters on p.nome.\n"
                                  "- For historical totals use t.valor_total or t.preco_unitario, not p.preco.\n"),
                    metadata={"type": "join_card",
                              "card_id": "clientes_transacoes_produtos_bridge",
                              "tables": ["clientes", "transacoes", "produtos"],
                              "relationship": "bridge"})]


def doc_key(d: Document) -> str:
    h = hashlib.sha256()
    h.update((d.page_content + "|" + str(d.metadata)).encode("utf-8"))
    return h.hexdigest()[:32]


def get_collection_uuid() -> str:
    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT uuid FROM public.langchain_pg_collection WHERE name = %s",
                (COLLECTION,),
            )
            row = cur.fetchone()
            if not row:
                # если коллекции ещё нет, принудительно создадим, добавив 0 доков
                vectorstore.add_documents([])
                cur.execute(
                    "SELECT uuid FROM public.langchain_pg_collection WHERE name = %s",
                    (COLLECTION,),
                )
                row = cur.fetchone()
            return row[0]


def get_existing_doc_keys(collection_uuid: str) -> Set[str]:
    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT (cmetadata->>'doc_key') AS doc_key
                FROM public.langchain_pg_embedding
                WHERE collection_id = %s
                  AND cmetadata ? 'doc_key'
                """,
                (collection_uuid,),
            )
            return {r[0] for r in cur.fetchall() if r[0]}


for d in kb_docs:
    d.metadata = d.metadata or {}
    d.metadata["doc_key"] = doc_key(d)

collection_uuid = get_collection_uuid()
existing_keys = set() if RAG_RESET else get_existing_doc_keys(collection_uuid)

new_docs: List[Document] = [d for d in kb_docs if d.metadata.get("doc_key") not in existing_keys]

if new_docs:
    vectorstore.add_documents(new_docs)

print("COLLECTION:", COLLECTION)
print("RAG_RESET:", RAG_RESET)
print("Existing keys:", len(existing_keys))
print("Inserted docs:", len(new_docs))
print("Total candidate docs:", len(kb_docs))