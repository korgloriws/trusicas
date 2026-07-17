# Trusicas

Interface web e CLI: letra em inglês → lição estruturada (JSON + abas) via **OpenRouter**, arquivos em `output/` e **histórico em SQLite** (pacote completo por música).

## Segurança

- **Nunca** commite o arquivo `.env` nem cole chaves em issues, chats ou código.
- Se uma chave já foi exposta em algum lugar público, **revogue imediatamente** no painel do OpenRouter e crie outra. Trate qualquer chave que apareceu em chat como comprometida.

## Início rápido (local, só Python)

Usa o **Python do PATH** (sem venv, sem scripts auxiliares).

1. Python 3.10+ instalado (`python --version`).

2. Entre na pasta do projeto:

```powershell
cd "c:\Users\mateus.rodrigues\Documents\mateus_augusto\ingles\trusicas"
```

3. **Uma vez**: instalar dependências no Python atual:

```powershell
python -m pip install -r requirements.txt
```

4. Copie `.env.example` para `.env` e preencha `OPENROUTER_API_KEY`, `TRUSICAS_ADMIN_PASSWORD` e `TRUSICAS_SECRET_KEY`.

5. Rode o gerador (CLI) ou a interface web (abaixo).

**CLI (exemplo):**

```powershell
python __main__.py --input sample-lyrics.txt --out-dir output --basename teste01
```

Ajuste o pool de modelos no `.env` (OpenRouter aceita no máximo **3** por pedido; se listar mais, escolhemos os melhores no momento):

```
OPENROUTER_MODELS=nvidia/nemotron-3-super-120b-a12b:free,qwen/qwen3-coder:free,openai/gpt-oss-20b:free,tencent/hy3:free,nvidia/nemotron-3-ultra-550b-a55b:free
OPENROUTER_ROUTE_SORT=throughput
```

Para forçar um único modelo numa geração: `--model outro/modelo`.
### Interface web (HTML + Flask, servido pelo Python)

Na pasta `trusicas`:

```powershell
python web.py
```

Abra no navegador: `http://127.0.0.1:5050/`

- **Visitantes (sem login):** podem ver a **Biblioteca** e abrir lições em **modo leitura**.
- **Admin:** no topo, **Senha admin → Entrar**. Com sessão activa: gerar lições, editar abas, guardar e excluir.
- **Nova lição:** só admin — gera e **salva automaticamente** no SQLite.
- **Biblioteca:** clique na linha para ver; **Editar** / **Excluir** só para admin.
- **Tema:** seletor **Escuro / Claro** no topo (preferência guardada em `localStorage` com a chave `trusicas-theme`).

Variáveis opcionais: `PORT` (default `5050`), `FLASK_HOST` (default `127.0.0.1`), `FLASK_DEBUG` (`1` para modo debug).

### Permissões (admin)

| Ação | Visitante | Admin (sessão) |
|------|-----------|----------------|
| Ver biblioteca e lições | Sim | Sim |
| Gerar / criar lição | Não | Sim |
| Editar e guardar | Não | Sim |
| Excluir | Não | Sim |

Configure no `.env`:

- `TRUSICAS_ADMIN_PASSWORD` — obrigatório para permitir edição
- `TRUSICAS_SECRET_KEY` — assina o cookie de sessão (use valor aleatório em produção)
- `TRUSICAS_ADMIN_SESSION_HOURS` — opcional (default `168`)

## Banco local (SQLite)

- Arquivo padrão: `trusicas/data/lessons.sqlite` (pasta `data/` é ignorada pelo Git).
- Caminho customizado: variável `TRUSICAS_DB` no `.env` (caminho absoluto recomendado).
- O **CLI** também grava no SQLite após gerar com sucesso (além dos arquivos em `output/`).

## Uso (referência — CLI)

Ler letra de um arquivo:

```powershell
python __main__.py --input ..\minha-letra.txt --out-dir output --basename lesson-01
```

Ler letra pelo stdin (PowerShell):

```powershell
Get-Content ..\minha-letra.txt -Raw | python __main__.py --out-dir output --basename lesson-01
```

Saídas:

- `output/lesson-01.raw.txt` — resposta bruta do modelo (debug)
- `output/lesson-01.lesson.json` — JSON parseado (quando válido)
- `output/lesson-01.lesson.md` — Markdown gerado a partir do JSON

Se o modelo devolver JSON inválido, o `.raw.txt` ajuda a ajustar o prompt ou trocar de modelo.

## Trocar de modelo

Defina `OPENROUTER_MODELS` (lista) no `.env` para routing automático, ou `OPENROUTER_MODEL` para um só modelo. Na CLI: `--model outro/modelo`.
## Opcional: ambiente virtual (venv)

Se mais tarde você quiser **isolar** dependências deste projeto:

```powershell
cd trusicas
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python __main__.py --help
```

## Próximos passos (ideia)

- Rodar **spaCy** localmente e mesclar análise sintática com o JSON do modelo.
- Exportar da web para `.md` / busca na biblioteca.
