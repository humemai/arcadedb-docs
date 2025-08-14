# ArcadeDB Instruction Primer (Condensed for LLM Prompts)

Goal: Enable reasoning, modeling, querying, schema / index design, performance tuning, and safe mutation of an ArcadeDB database without consulting external docs.

---

## 1. Essence

ArcadeDB = Multi-Model, single kernel (no adapters). Supported models:
- Graph (property graph: Vertices + Edges, edges have direction, LIFO ordering)
- Document (schema-less / hybrid / schema-full)
- Key/Value (bucket + key -> record (doc or graph elem))
- Full-Text Search (LSM-based full-text index via Lucene tokenization)
- Vector (HNSW ANN)
- (Time-Series / Vector Embedding – WIP indicators 🚧 where noted)

Storage: LSM-Tree indexes + paged files. Record-level addressing via immutable Record IDs (RIDs) of form `#<bucketId>:<position>`; O(1) direct lookup.

No SQL JOINs. Relationships =:
- Edge (graph-native, bidirectional adjacency, O(1) traversal)
- LINK (reference field storing RID, unidirectional)
- EMBEDDED (inline value, no RID, owned lifecycle)

---

## 2. Core Primitives

Record types:
- Document
- Vertex (Document + adjacency lists)
- Edge (Document linking exactly two vertices; direction out->in; internally also stores reverse adjacency)
Common immutable attributes: `@rid`, `@type`; graph metadata: `@in`, `@out` (implicit unless made explicit for constraints).

RID: Unique, never reused; `#-1:-1` = null RID sentinel.

### Types & Buckets
- Type ~ table/class; has 1..N buckets (physical files)
- Bucket: exclusive to one type; name pattern `<TypeName>_<ordinal>`
- Bucket selection strategies:
  - `round-robin` (default)
  - `thread` (maps thread id to bucket; reduces contention on parallel inserts)
  - `partitioned(<primary-key-field>)` (consistent hashing; speeds point lookup across many buckets)
  
### Inheritance
- Multiple inheritance supported (`ALTER TYPE ... SUPERTYPE +A -B`)
- Queries are polymorphic by default (`SELECT FROM BaseType` scans sub-type buckets)

### Embedded vs LINK vs EDGE (decision)
- Use EMBEDDED for strong ownership, small bounded sub-objects, no external references
- Use LINK for reference semantics / one-way navigation
- Use EDGE when:
  - Need traversal performance / pattern queries
  - Relationship carries its own properties
  - Large fan-out / graph algorithms

Edges are always internally bidirectional (index-free adjacency) even if conceptually modeled as one direction.

---

## 3. Schema

Create & evolve:
- `CREATE TYPE <Name> [BUCKETS <n>] [EXTENDS <Super>] [VERTEX|EDGE|DOCUMENT]`
- `CREATE PROPERTY <Type>.<prop> <DataType> [MANDATORY] [NOTNULL] [READONLY] [DEFAULT <expr>]`
- `ALTER TYPE / ALTER PROPERTY` mutate metadata
- Data types: BOOLEAN, BYTE, SHORT, INTEGER, LONG, FLOAT, DOUBLE, DECIMAL, STRING, DATE, DATETIME, BINARY, LINK, LIST, SET, MAP, EMBEDDED
- Constraints enforced at write (except Gremlin may violate if sets after create—prefer SQL/API when constrained)
- Drop schema elements: `DROP PROPERTY`, `DROP TYPE` (must remove instances first or truncate)
- Truncate physical data: `TRUNCATE TYPE`, `TRUNCATE BUCKET` (UNSAFE on vertex/edge)

Custom property metadata allowed (key -> value); set to `null` to remove.

---

## 4. Buckets & Data Locality Patterns

Design buckets to avoid global indexes when partition keys are natural:
- Time-sliced: `Invoice_2024`, query by bucket for year range
- Geo / domain partition
- Thread strategy for bulk parallel inserts
Explicit bucket targeting:
- Insert: `INSERT INTO BUCKET:Customer_Europe CONTENT {...}`
- Query: `SELECT FROM BUCKET:Invoice_2024`
Multiple buckets reduce write contention; monitor OS file descriptor limits.

---

## 5. Indexes

`CREATE INDEX <name> [ON <Type>(prop[,propN])] <type> [UNIQUE|NOTUNIQUE|FULLTEXT|VECTOR] [NULL_STRATEGY (IGNORE|ERROR)]`
- Automatic (with `ON`) vs Manual (no ON; you manage keys)
- Composite: list properties or key types
- Unique ignores null keys (unless `NULL_STRATEGY ERROR`)
- Full-text: single property only; Lucene tokenization; search with `CONTAINSTEXT`
- Vector (HNSW):
  - Create on: `<Type>(vectorProperty)` + store vector (float[] default)
  - Query: `SELECT vectorNeighbors('<IndexName>','<key>',k)`
  - Tunables (on creation or settings): `m` (max connections, default 16), `efConstruction` (build quality), `ef` (search breadth), `distanceFunction` (cosine, innerproduct, euclidean, etc.), `randomSeed`
- Rebuild only if corrupted: `REBUILD INDEX <name>|*`

Performance:
- Prefer partitioned bucket strategy + local sub-index over giant shared index when high write concurrency
- Avoid over-indexing (LSM write amplification)
- Use unique index for atomic UPSERT semantics (see §7.3)

---

## 6. Transactions & Concurrency

ACID, MVCC (optimistic):
- Isolation: READ_COMMITTED (default), REPEATABLE_READ. SERIALIZABLE unsupported.
- Writes checked at commit; conflict => `ConcurrentModificationException` -> retry strategy on client
- Transactions held in memory ⇒ huge batches may exceed heap; split logically
- Nested transactions supported (stack semantics)
- Remote HTTP commands outside local transaction scope unless explicit transaction endpoints (check client libs)
- RID direct read O(1); version check enforces MVCC

---

## 7. Querying

### Languages
- SQL (primary dialect)
- MATCH (pattern / graph)
- TRAVERSE (procedural graph walk; often replaceable by SELECT path chaining)
- Gremlin (via plugin or embedded)
- OpenCypher (subset)
- HTTP/JSON API (language param)
No JOIN. Use chained property access (`customer.address.city`) or graph functions / pattern match.

### 7.1 SQL Dialect Key Differences
- Projection `SELECT FROM Type` allowed (`*` implicit)
- No JOIN clause; relationships navigated via LINK path or graph expansion
- `DISTINCT` vs `distinct(expr)` function nuance (function limited scope)
- `UNWIND` & `expand()` flatten collections
- No `HAVING` (use nested SELECT with WHERE)
- Console single-line commands (multi-line comment unsupported)
- Functions & methods rich set (graph, math, collections, spatial, vector)

### 7.2 Execution Order
`FROM` (target/bucket/index) → `LET` (bindings, subqueries) → `WHERE` → `GROUP BY` → `UNWIND` / `EXPAND` → `ORDER BY` → `SKIP` → `LIMIT`

### 7.3 CRUD / DDL Commands (Core)
- Create schema: `CREATE TYPE`, `CREATE PROPERTY`, `ALTER TYPE/PROPERTY`, `DROP *`
- Data:  
  - Insert document: `INSERT INTO Type SET a=1` or `INSERT INTO Type CONTENT {json}`  
  - Vertex: `CREATE VERTEX Type SET ...` or via `INSERT`  
  - Edge: `CREATE EDGE RelType FROM <rid|subquery|[rids]> TO <...> [SET props] [BATCH n] [BIDIRECTIONAL true]`
- Update:  
  - `UPDATE Type SET a=1, list += 'x', list -= 'y', map.put('k','v') WHERE ...`
  - `UPDATE Type UPSERT SET ... WHERE uniqueField = value` (requires unique index for atomicity)
- Delete: `DELETE FROM Type WHERE ...` (edges auto-maintain adjacency)
- Move repartition: `MOVE VERTEX (SELECT ...) TO TYPE:NewType|BUCKET:Name [BATCH n]`
- Truncate: `TRUNCATE TYPE X` (fast physical), `TRUNCATE BUCKET Y`
- Backup/Export: `BACKUP DATABASE [<path>]`, `EXPORT DATABASE`
- Import: `IMPORT DATABASE <url> [WITH setting=value,...]` (CSV, JSONL, GraphML, GraphSON, RDF, Neo4j JSONL, OrientDB export, CSV split modes)
- Integrity: `CHECK DATABASE [FIX] [COMPRESS]`
- HA alignment: `ALIGN DATABASE` (leader only)

### 7.4 Graph Access Helpers (Functions)
- `out(), in(), both(), outE(), inE(), bothE(), outV(), inV(), bothV()`
- Path algorithms: `shortestPath()`, `dijkstra()`, `astar()`
- Pattern search: `MATCH` with arrow syntax `()-[:Rel]->()`, variable binding `as:` alias, negative patterns with `NOT`, multi-clauses intersection semantics
- Depth / loops: MATCH `while:` and TRAVERSE `WHILE $depth <= N` or `MAXDEPTH N`

### 7.5 Pagination
- Offset: `SKIP n LIMIT m` (re-scans; slower large n)
- Seek: `WHERE @rid > <lastRid> LIMIT pageSize` (fast; keep last RID)

### 7.6 Variables & Attributes
- Record attrs: `@rid`, `@type`, `@size`, `@in`, `@out`, `@this`
- Context variables: `$current`, `$parent`, `$depth`, `$path`, `$history`, `$matched` (MATCH), `$stack`
- Use `LET $var = (SELECT ...)` inside SELECT or SQL script
- Methods: `asType()`, `size()`, `keys()`, `values()`, `include()/exclude()`, collection transforms, string ops, geometry, normalization, `type()`, `hash()`, vector/spatial, `precision()`, `encode()/decode()`

### 7.7 Functions (Categories)
- Aggregates: `count, sum, avg, min, max, median, mode, stddev, variance, percentile, bool_and, bool_or`
- Collections: `unionall, intersect, difference, symmetricDifference, set(), list(), map(), first(), last(), expand(), distinct()`
- Graph: `in/out/both variants`, path algos above
- Spatial/Geometry: `point, circle, rectangle, lineString, polygon, distance, isWithin(), intersectsWith()`
- Vector: `vectorNeighbors(indexName,key,k)`
- Temporal: `date(), sysdate(), duration(), precision()`
- Conditional / utility: `if, ifnull, ifempty, coalesce, format, encode/decode, uuid, randomInt`
- Security / meta: `version()`

### 7.8 Filtering Operators
- Standard: =, <=> (null-safe), !=, <, <=, >, >=, BETWEEN, IS [NOT] NULL
- Pattern: LIKE (case-sensitive), ILIKE (case-insensitive), MATCHES (regex), CONTAINSTEXT (full-text / fallback substring)
- Collections: IN, NOT IN, CONTAINS, CONTAINSALL, CONTAINSANY, CONTAINSKEY, CONTAINSVALUE
- Graph semantics: use RID sets, adjacency functions
- INSTANCEOF for type hierarchy
- Wildcards: `%` multi-char, `?` single-char (escape with backslash)

### 7.9 UPSERT Semantics
Only atomic if WHERE predicate resolves via UNIQUE index. Pattern:
```
UPDATE Type SET ... UPSERT WHERE uniqueField = :value
```

---

## 8. Vector / ANN
- Store float arrays in a property (default vectorType `float`)
- Create HNSW index (UNIQUE not applicable)
- Query approximate neighbors: `SELECT vectorNeighbors('Type[prop]', 'queryKey', k)`
- Parameters:
  - `distanceFunction`: cosine, innerproduct, euclidean, correlation, manhattan, canberra, chebyshev, braycurtis
  - `m`, `efConstruction`, `ef`, `randomSeed`

---

## 9. Importing Data
Importer auto-detects format; key settings: `forceDatabaseCreate`, `commitEvery`, `parallel`, `mapping` (JSON remap rules), `documents/vertices/edges` split, CSV schema inference limits, vector settings, `idProperty`.
UPSERT-like uniqueness via `typeIdProperty` + index options.

---

## 10. Security Model (Server Mode)

File-based users + groups (`server-users.jsonl`, `server-groups.json`):
- User -> database-specific group assignments (fallback `*`)
- Group permissions (granular):
  - Schema: createType, dropType, createProperty, dropProperty, alter*
  - Data: createRecord, readRecord, updateRecord, deleteRecord
  - Index: createIndex, dropIndex
  - Security: createUser, dropUser, etc. (admin group)
  - Script & function invocation can be restricted
- Result limiting: `resultSetLimit`
- Append-only pattern: allow create/read; disallow update/delete
Embedded mode: no security unless server started in-process.

Secrets passing: prefer file-based password env (e.g. `arcadedb.server.rootPasswordPath`).

---

## 11. High Availability

Architecture: RAFT-based leader election; leader handles coordination of writes; replicas forward write requests transparently; reads served locally.

Key settings:
- `arcadedb.ha.clusterName`
- `arcadedb.ha.serverList` (if not auto-discovery)
- `arcadedb.ha.quorum` = majority|all|none|N
- Ports: replication incoming range (default 2424-2433)
- Alignment: `ALIGN DATABASE` to reconcile diverged files (checksum + file transfer)
Quorum failure ⇒ transaction abort (atomic cluster-wide).

Consistency: Leader-serialized writes; replicas eventually consistent (normally fast).

---

## 12. Performance Guidelines

Area | Guidance
-----|---------
Concurrency | Use multiple buckets + `thread` strategy for parallel inserts
Lookups | Prefer RID direct access or partitioned index; design unique fields with partitioned buckets
Indexes | Only what queries need; composite for combined filters; vector index for ANN only
Graph Traversal | Use MATCH for pattern constraints; SELECT chaining for shallow known-depth; TRAVERSE sparingly
Pagination | RID seek method for large scans
Batching | Use `BATCH` in CREATE EDGE; importer `commitEvery`
Retry | Capture `ConcurrentModificationException` and retry limited times
Memory | Large transactions split; low-ram profile for constrained env
Avoid | Overuse of `expand()` on huge collections; full scans without predicates; `SKIP` with huge offsets
Edge Fanout | Create directional filters; maybe model alternate relationship forms if skewed

---

## 13. Integrity & Maintenance

- `CHECK DATABASE [FIX]` detects orphan edges / invalid links, can auto-remove broken edges
- `BACKUP DATABASE` non-blocking snapshot (no WAL spill)
- `RESTORE` offline into target directory (restart needed)
- `EXPORT/IMPORT` logical portability across versions / downgrade path
- Auto-upgrade on newer binary (semantic versioning: patch compatible both ways)

---

## 14. Limitations / Caveats

- No SQL JOIN; must model relationships explicitly
- No SERIALIZABLE isolation
- Distinct function vs keyword semantic difference
- Console: single-line; no multiline comments
- Full-text: single property only
- Edge creation = creates edge + updates both vertices (requires appropriate permissions)
- `expand()` cannot co-exist with GROUP BY in same projection
- UPSERT safe only with unique index predicate
- Hidden property flag currently inert
- Gremlin property-setting may violate immediate constraints (deferred assignment) — use SQL for constrained types

---

## 15. Modeling Checklist

Question | Choice
---------|-------
Need fast traversals with properties on relationship? | EDGE
Small immutable sub-object owned by parent? | EMBEDDED
Shared entity referenced many places? | LINK
High-write partition key present? | Multi-bucket + partition/thread strategy
Need historical validity? | Edge with from/to date + index
Need nearest neighbor search? | Vector property + HNSW index
Need text relevance? | FULLTEXT index + CONTAINSTEXT
Require atomic unique upsert? | UNIQUE index + UPDATE ... UPSERT

---

## 16. Common Snippets

DDL:
```
CREATE TYPE Person EXTENDS VERTEX;
CREATE PROPERTY Person.name STRING MANDATORY;
CREATE INDEX Person.name ON Person(name) UNIQUE;
ALTER TYPE Person BUCKETSELECTIONSTRATEGY thread;
```

Insert & Edge:
```
CREATE VERTEX Person SET name='Alice';
CREATE VERTEX Person SET name='Bob';
CREATE EDGE Knows FROM (SELECT FROM Person WHERE name='Alice')
                 TO   (SELECT FROM Person WHERE name='Bob')
                 SET since=2024;
```

Upsert:
```
UPDATE Person SET age=42 UPSERT WHERE name='Alice';
```

Traversal (pattern):
```
MATCH {type:Person, as:a, where:(name='Alice')}
      .out('Knows'){as:b, while:$depth<3}
RETURN DISTINCT a.name, b.name
```

Vector neighbors:
```
SELECT vectorNeighbors('Word[vector]','king',5);
```

Pagination (seek):
```
SELECT FROM Person WHERE @rid > #12:345 LIMIT 50;
```

Shortest path:
```
SELECT shortestPath(#20:1,#20:99,'OUT','Knows');
```

Integrity check:
```
CHECK DATABASE FIX;
```

---

## 17. Safety / Validation Tips

- Always constrain UPDATE / DELETE with predicates or RIDs
- Prefer prepared/parameterized queries (no string concat) for dynamic values
- Validate vector dimension consistency before insert
- Use group permissions to enforce append-only or read-only roles
- Run `CHECK DATABASE` post bulk import if bypassing application validation
- Use RID seek pagination to avoid inconsistent pages after concurrent writes (stateful cursorless)

---

## 18. Quick Reference Tables

### Graph Functions
`out() in() both() outE() inE() bothE() outV() inV() bothV() shortestPath() dijkstra() astar()`

### Key Record Attributes
`@rid @type @size @in @out`

### Context Variables
`$current $parent $depth $path $history $matched`

### Critical Commands
Create: `CREATE TYPE / PROPERTY / INDEX / VERTEX / EDGE / BUCKET`  
Mutate: `ALTER TYPE / PROPERTY`, `UPDATE [UPSERT]`  
Read: `SELECT`, `MATCH`, `TRAVERSE`  
Graph Ops: `CREATE EDGE`, path functions  
Maintenance: `CHECK DATABASE`, `ALIGN DATABASE`, `BACKUP DATABASE`, `REBUILD INDEX`  
Destructive: `DELETE`, `TRUNCATE TYPE/BUCKET`, `DROP *`

---

## 19. When to Use Which Traversal Form

Need | Use
-----|----
Fixed shallow hops | `SELECT out().out()...`
Pattern with conditions on nodes/edges | `MATCH`
Arbitrary depth with loop condition | `MATCH while:` or `TRAVERSE`
Metrics/aggregation after traversal | Subquery `MATCH` inside `SELECT`
Ad-hoc analytic path cost | `dijkstra()/astar()/shortestPath()`

---

## 20. Minimal Mental Model

1. Everything addressable by immutable RID; direct RID operations are fastest.
2. Modeling relationships explicitly (edges or links) eliminates need for joins.
3. Buckets = physical sharding; strategy + partition key critical to concurrency and index efficiency.
4. MVCC optimistic commit + per-page versioning ensures isolation; design retries.
5. MATCH for declarative graph patterns; SELECT for projection/aggregation; combine via subqueries.
6. Index only for selective predicates; composite & partitioned patterns reduce page reads.
7. Vector + HNSW for ANN; tune ef / m for recall vs memory.
8. Security enforced only in server context; embed implies trusted code.
9. HA write path always coordinated through leader; clients can stay on any node.
10. Large-scale ingestion: multi-bucket + thread strategy + batched edges + importer with commitEvery.

Use this distilled reference to drive answer synthesis, validation, generation, and reasoning about ArcadeDB operations.

