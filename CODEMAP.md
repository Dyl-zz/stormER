# CODEMAP.md — code map. Reader: coding agent only. Rules: CLAUDE.md §19.
# (Formerly FRAMEWORK.md; 'framework' is now a product concept — CLAUDE.md §2/§7.)
# FORMAT (locked): path :: symbol(args) -> ret :: purpose-tag(<=8 words)
# One symbol/line. Public only. Sorted by path within section. Update same commit.
# Entry states: (no mark)=implemented  [PLANNED]=not yet written, signature is intent.
# Before writing ANY new function: grep this file first. Reuse > wrap > reimplement.

## FOUNDATION
app/config.py :: Settings :: pydantic-settings, env prefix RC_
app/config.py :: get_settings() -> Settings :: cached settings singleton
app/db.py :: Base :: declarative base, all tables
app/db.py :: get_engine() -> AsyncEngine :: lazy global engine
app/db.py :: get_session() -> AsyncIterator[AsyncSession] :: FastAPI session dependency
app/db.py :: get_session_factory() -> async_sessionmaker :: lazy global session factory
app/db.py :: json_column_type() -> JSON :: JSONB on postgres, JSON elsewhere
app/seed.py :: seed() -> None :: create single MVP user

## SPINE/MODELS (SQLAlchemy tables)
app/spine/models/branch.py :: Branch(document_id, name, color, ordinal) :: branch row, owns color
app/spine/models/center.py :: CenterNode(document_id, markdown) :: thesis content, chips as {{chip:marker}} tokens
app/spine/models/chip.py :: ReferenceChip(center_node_id, node_id, execution_id, marker) :: structured citation
app/spine/models/document.py :: Document(owner_id, title, created_at) :: one radial canvas
app/spine/models/execution.py :: ExecutionLabel :: enum draft|milestone
app/spine/models/execution.py :: ExecutionStatus :: enum queued|running|succeeded|failed
app/spine/models/execution.py :: NodeExecution(node_id, status, label, created_at, started_at, finished_at, generated_text, input_keys, error, raw_data_uri, raw_data_state, raw_data_meta) :: immutable run record
app/spine/models/execution.py :: RawDataState :: enum present|swept|unrecoverable
app/spine/models/execution.py :: uq_one_inflight_per_node :: partial unique index, §10 concurrency
app/spine/models/framework.py :: Framework(owner_id, org_id, name, structure) :: serialized doc structure, no live FKs
app/spine/models/node.py :: Node(branch_id, type, title, ordinal, parameters, declared_inputs) :: recipe, never results
app/spine/models/rag_chunk.py :: EMBEDDING_DIM :: 256, hash-embedder width
app/spine/models/rag_chunk.py :: RagChunk(execution_id, ordinal, text, embedding) :: derived index, dies with blob
app/spine/models/template.py :: Template(owner_id, org_id, name, type, parameters, declared_inputs, color_tag) :: serialized recipe, no FKs to node/execution
app/spine/models/user.py :: User(id, display_name, email) :: seeded single row MVP

## SPINE/CORE (interfaces + engine)
app/spine/blobstore.py :: BlobNotFound :: missing-blob exception
app/spine/blobstore.py :: BlobStore.delete(uri) -> None :: sweep support, idempotent
app/spine/blobstore.py :: BlobStore.get(uri) -> bytes :: read raw blob
app/spine/blobstore.py :: BlobStore.put(key, data, meta) -> str :: write blob FIRST, returns uri
app/spine/blobstore.py :: LocalBlobStore(root) :: filesystem impl, MVP deployment + dev
app/spine/blobstore.py :: blob_meta(data, mime_type) -> dict :: standard raw_data_meta hash/size/mime
app/spine/copy.py :: copy_node(session, node_id, dest_branch_id, blobstore) -> Node :: node-copy, carries latest succeeded execution, duplicates blob
app/spine/executions.py :: InFlightConflict(node_id, execution_id) :: 409 carrier
app/spine/executions.py :: JobEnqueuer.enqueue_run_node(execution_id, resolved_inputs) -> None :: queue protocol, spine never imports ARQ
app/spine/executions.py :: PromoteError :: promote on non-succeeded execution
app/spine/executions.py :: RunReport(created, skipped) :: batch re-run result
app/spine/executions.py :: SkippedNode(node_id, reason) :: skip reason snapshot|in_flight
app/spine/executions.py :: complete_execution(session, execution, result, raw_data_uri, raw_data_meta) -> None :: freeze success, requires blob uri
app/spine/executions.py :: enqueue_run(session, node_id, enqueuer, resolved_inputs) -> NodeExecution :: queued row + job, raises InFlightConflict
app/spine/executions.py :: fail_execution(session, execution, code, message, retryable) -> None :: failures write rows, structured error
app/spine/executions.py :: latest_execution(session, node_id) -> NodeExecution|None :: latest by created_at, status=succeeded ONLY
app/spine/executions.py :: mark_running(session, execution) -> None :: queued -> running transition
app/spine/executions.py :: promote(session, execution_id) -> NodeExecution :: label draft->milestone, sole label mutation
app/spine/executions.py :: run_document(session, document_id, enqueuer) -> RunReport :: batch enqueue, skips snapshot/in_flight with reasons
app/spine/executions.py :: utcnow() -> datetime :: tz-aware now
app/spine/export.py :: render_txt(session, document_id, now) -> str :: pure fn, deterministic, golden-file tested
app/spine/frameworks.py :: BindingField(branch_ordinal, node_ordinal, node_title, field) :: one binding slot
app/spine/frameworks.py :: BindingValue(branch_ordinal, node_ordinal, field, value) :: filled binding
app/spine/frameworks.py :: collect_bindings(structure) -> list[BindingField] :: generic binding-field harvest, spine-blind to CIK
app/spine/frameworks.py :: instantiate_framework(session, framework_id, owner_id, title, bindings) -> Document :: empty nodes, bindings filled
app/spine/frameworks.py :: save_as_framework(session, document_id, owner_id, name) -> Framework :: drops executions + center content ALWAYS
app/spine/runner.py :: InputKeysFormatter.format_input_keys(input_keys) -> str :: runner-provided, export's only runner touchpoint
app/spine/runner.py :: NodeRunner.on_success(execution_id, result, raw) -> None :: post-commit hook for derived data
app/spine/runner.py :: NodeRunner.run(parameters, resolved_inputs) -> tuple[StructuredResult, bytes] :: the generalization seam
app/spine/runner.py :: NodeRunner.validate_input_keys(input_keys) -> dict :: per-type input_keys model check
app/spine/runner.py :: NodeRunner.validate_parameters(parameters) -> dict :: per-type params model check
app/spine/runner.py :: RetentionPolicy(draft_blob_ttl_days, milestone_blob_ttl_days, swept_state) :: per-type blob retention
app/spine/runner.py :: RunnerError(code, message, retryable) :: expected runner failure
app/spine/runner.py :: StructuredResult(generated_text, input_keys, raw_mime_type) :: runner output, small part
app/spine/runner.py :: binding_fields(params_model) -> list[str] :: fields marked binding=True
app/spine/runner.py :: get_runner(node_type) -> NodeRunner :: registry lookup
app/spine/runner.py :: register_runner(runner) -> None :: registry insert, called at startup
app/spine/sweep.py :: SweepReport(swept, unrecoverable) :: sweep_blobs result
app/spine/sweep.py :: sweep_blobs(session, blobstore, now) -> SweepReport :: per-type retention, sets raw_data_state, kills chunks
app/spine/sweep.py :: sweep_stuck(session, now) -> int :: running > timeout*2 -> failed/worker_lost
app/spine/templates.py :: instantiate_template(session, template_id, branch_id) -> Node :: mints EMPTY node
app/spine/templates.py :: save_as_template(session, node_id, owner_id, name) -> Template :: drops executions ALWAYS

## RUNNERS (equity vertical — the ONLY equity-aware layer)
app/runners/__init__.py :: register_all(settings, session_factory, blobstore) -> None :: startup wiring, spine stays import-free
app/runners/edgar.py :: EdgarInputKeys(cik, accession_no, form_type, filing_date) :: re-fetchable identity
app/runners/edgar.py :: EdgarParams(cik, form_type, section, date_range) :: pydantic, cik is binding field
app/runners/edgar.py :: EdgarRunner(settings) :: flagship; ALL filing mess contained here
app/runners/edgar.py :: SECTIONS :: 10-K item slicing patterns
app/runners/upload_rag.py :: HashEmbedder.embed(text) -> list[float] :: deterministic local embedding, no API
app/runners/upload_rag.py :: RagInputKeys(index_execution_id, content_hash, filename) :: points at indexing run
app/runners/upload_rag.py :: RagParams(question, top_k) :: query params
app/runners/upload_rag.py :: UploadRagRunner(settings, session_factory, blobstore) :: index exec + question execs, static snapshot
app/runners/upload_rag.py :: chunk_text(text) -> list[str] :: paragraph-respecting 1200-char chunks
app/runners/websearch.py :: WebSearchInputKeys(query, source_urls, retrieved_at) :: sources MUST be stored
app/runners/websearch.py :: WebSearchParams(query) :: search params
app/runners/websearch.py :: WebSearchRunner(settings) :: draft research via Anthropic web_search tool

## API (FastAPI, /api/v1, conventions: CLAUDE.md §13)
app/api/deps.py :: get_blobstore() -> BlobStore :: lazy LocalBlobStore singleton
app/api/deps.py :: get_current_user(session) -> User :: seeded user MVP; auth swap point
app/api/errors.py :: ApiError(status_code, code, message, detail) :: raisable envelope error
app/api/errors.py :: error_envelope(code, message, detail) -> dict :: single envelope everywhere
app/api/errors.py :: install_handlers(app) -> None :: ApiError + validation handlers
app/api/routes/documents.py :: GET /documents/{id} :: full canvas: branches + nodes + center
app/api/routes/documents.py :: GET /documents/{id}/export?format=txt :: streams render_txt, txt only
app/api/routes/documents.py :: POST /documents :: create document + center node
app/api/routes/documents.py :: POST /documents/{id}/branches :: append branch with color
app/api/routes/documents.py :: POST /documents/{id}/chips :: mint structured reference chip
app/api/routes/documents.py :: POST /documents/{id}/run -> 202 :: batch re-run, returns created+skipped
app/api/routes/documents.py :: POST /documents/{id}/save-framework :: structure only
app/api/routes/documents.py :: PUT /documents/{id}/center :: update thesis markdown
app/api/routes/executions.py :: GET /executions/{id} :: poll target, 2s client poll
app/api/routes/executions.py :: GET /nodes/{id}/executions?after=&limit= :: keyset paginated, default 20 max 100
app/api/routes/executions.py :: POST /executions/{id}/promote :: label promotion only
app/api/routes/executions.py :: POST /nodes/{id}/executions -> 202 :: enqueue run, 409 if in-flight
app/api/routes/frameworks.py :: GET /frameworks/{id}/bindings :: binding slots to prompt for
app/api/routes/frameworks.py :: POST /frameworks/{id}/instantiate :: new document from bindings
app/api/routes/nodes.py :: PATCH /nodes/{id} :: edit recipe, never touches executions
app/api/routes/nodes.py :: POST /branches/{id}/nodes :: create node, params validated per-type
app/api/routes/nodes.py :: POST /nodes/{id}/copy :: node-copy, only execution-carrying path
app/api/routes/nodes.py :: POST /nodes/{id}/save-template :: drops executions always
app/api/routes/nodes.py :: POST /nodes/{id}/upload -> 202 :: stage file, enqueue indexing execution
app/api/routes/nodes.py :: POST /templates/{id}/instantiate :: mint empty node from template
app/api/serializers.py :: branch_json/chip_json/document_json/execution_json/node_json(row) -> dict :: row to JSON
app/main.py :: app :: module-level ASGI app
app/main.py :: create_app() -> FastAPI :: app factory, registers runners on lifespan

## WORKER (ARQ)
app/worker/queue.py :: ArqEnqueuer.enqueue_run_node(execution_id, resolved_inputs) -> None :: JobEnqueuer impl over redis
app/worker/queue.py :: get_enqueuer() -> ArqEnqueuer :: FastAPI dependency, shared pool
app/worker/tasks.py :: WorkerSettings :: ARQ settings, cron sweeps, startup wiring
app/worker/tasks.py :: execute_node(session, blobstore, execution_id, resolved_inputs) -> None :: the mutation window; blob FIRST then row
app/worker/tasks.py :: run_node(ctx, execution_id, resolved_inputs) :: ARQ task wrapper
app/worker/tasks.py :: sweep_blobs_task(ctx) / sweep_stuck_task(ctx) :: cron entrypoints
