/* =========================================================
   開発の手引き ビルダー
   _src/issue-*.json（#1〜#9・番号つき）と _src2/*.json（その他Issue）を読み、
   各ガイドの HTML と index.html（ポータル）を生成する。
   実行: node _build.js  （docs/開発の手引き/ で）
   ========================================================= */
const fs = require("fs");
const path = require("path");

const DIR = __dirname;
const SRC = path.join(DIR, "_src");
const SRC2 = path.join(DIR, "_src2");

// ---- インライン記法（`code` **bold** [text](url)）→ HTML ----
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function inline(s) {
  let t = esc(s);
  t = t.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
    (_, txt, url) => `<a href="${url}" target="_blank" rel="noopener">${txt}</a>`);
  t = t.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
  t = t.replace(/\*\*([^*]+)\*\*/g, (_, b) => `<strong>${b}</strong>`);
  t = t.replace(/\n/g, "<br>");
  return t;
}

// ---- ブロック描画 ----
function block(b) {
  switch (b.type) {
    case "p":
      return `<p>${inline(b.text)}</p>`;
    case "ul":
      return `<ul>${(b.items || []).map(i => `<li>${inline(i)}</li>`).join("")}</ul>`;
    case "ol":
      return `<ol>${(b.items || []).map(i => `<li>${inline(i)}</li>`).join("")}</ol>`;
    case "checklist":
      return `<ul class="checklist">${(b.items || []).map(i => `<li>${inline(i)}</li>`).join("")}</ul>`;
    case "steps":
      return `<ol class="steps">${(b.items || []).map(s =>
        `<li><span class="st-title">${inline(s.title)}</span>` +
        (s.detail ? `<div class="st-detail">${inline(s.detail)}</div>` : "") + `</li>`).join("")}</ol>`;
    case "code":
      return `<pre><code>${esc(b.code)}</code></pre>`;
    case "callout": {
      const v = ["tip", "warn", "danger", "ok", "study"].includes(b.variant) ? b.variant : "tip";
      const label = b.label ? `<span class="label">${inline(b.label)}</span>` : "";
      return `<div class="callout ${v}">${label}<p>${inline(b.text)}</p></div>`;
    }
    case "table": {
      const head = `<tr>${(b.headers || []).map(h => `<th>${inline(h)}</th>`).join("")}</tr>`;
      const rows = (b.rows || []).map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join("")}</tr>`).join("");
      return `<table>${head}${rows}</table>`;
    }
    case "research":
      return `<ul class="research">${(b.items || []).map(r =>
        `<li><div class="r-topic">${inline(r.topic)}</div>` +
        (r.why ? `<div class="r-why">${inline(r.why)}</div>` : "") +
        (r.where ? `<div class="r-where">${inline(r.where)}</div>` : "") + `</li>`).join("")}</ul>`;
    default:
      return b.text ? `<p>${inline(b.text)}</p>` : "";
  }
}

const HERO_CLASS = { "アプリ": "", "ロボ": "robot", "基盤": "done", "ハード": "hw", "結合": "integ", "COULD": "could" };
const CARD_CLASS = { "アプリ": "", "ロボ": "robot", "基盤": "", "ハード": "hw", "結合": "integ", "COULD": "could" };

function fileNameOf(d) {
  return (typeof d.issueNo === "number")
    ? `issue-${String(d.issueNo).padStart(2, "0")}.html`
    : `${d.id}.html`;
}

function renderItem(d) {
  const heroClass = HERO_CLASS[d.category] || "";
  const numbered = typeof d.issueNo === "number";
  const headTitle = numbered
    ? `Issue #${d.issueNo}　${esc(d.taskName)}${d.screen ? `（${esc(d.screen)}）` : ""}`
    : `${esc(d.taskName)}${d.screen ? `（${esc(d.screen)}）` : ""}`;
  const titleTag = numbered
    ? `#${d.issueNo} ${esc(d.taskName)}｜開発の手引き`
    : `${esc(d.taskName)}｜開発の手引き`;

  const pills = [];
  if (d.screen) pills.push(`<span class="pill">画面 ${esc(d.screen)}</span>`);
  if (d.role) pills.push(`<span class="pill role">担当：${esc(d.role)}</span>`);
  if (d.sprint) pills.push(`<span class="pill sprint">${esc(d.sprint)}</span>`);
  if (d.priority) pills.push(`<span class="pill prio">優先 ${esc(d.priority)}</span>`);
  if (d.status) pills.push(`<span class="pill done">${esc(d.status)}</span>`);
  (d.stack || []).forEach(s => pills.push(`<span class="pill lang">${esc(s)}</span>`));

  const toc = d.sections.map((s, i) =>
    `<li><a href="#sec-${i}">${inline(s.heading)}</a></li>`).join("");
  const body = d.sections.map((s, i) =>
    `<section id="sec-${i}">\n  <h2 class="sec">${inline(s.heading)}</h2>\n  ` +
    (s.blocks || []).map(block).join("\n  ") + `\n</section>`).join("\n\n");

  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${titleTag}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="wrap">

  <nav class="crumbs"><a href="index.html">← 開発の手引き トップ</a></nav>

  <header class="hero ${heroClass}">
    <h1>${headTitle}</h1>
    <p>${inline(d.oneLineGoal)}</p>
    <div class="badges">
      ${d.role ? `<span class="badge">担当：${esc(d.role)}</span>` : ""}
      ${d.sprint ? `<span class="badge">${esc(d.sprint)}</span>` : ""}
      ${d.priority ? `<span class="badge">優先 ${esc(d.priority)}</span>` : ""}
      ${d.status ? `<span class="badge">${esc(d.status)}</span>` : ""}
    </div>
  </header>

  <div class="meta-bar">${pills.join("\n    ")}</div>

  <nav class="toc">
    <h2>📑 このページの内容</h2>
    <ol>${toc}</ol>
  </nav>

  <div class="callout study">
    <span class="label">▶ 進める前に（全Issue共通）</span>
    <p>作業は <code>main</code> から <strong>ブランチを切って</strong> 進めます。<code>main</code> へ直接 push は禁止。完成したら Pull Request → PM（水谷）レビュー → マージ。</p>
    <pre><code>git switch main &amp;&amp; git pull        # 最新を取り込む
git switch -c feature/◯◯           # このIssue用のブランチを作る（ここから作業）
# （資料を見ながら実装）→ npm run dev で確認
git add . &amp;&amp; git commit -m "やったことを1行で"
git push -u origin feature/◯◯      # → GitHub で Pull Request</code></pre>
    <p>コマンドの全手順・GitHub Desktop での方法は <a href="環境構築ガイド.html#flow">環境構築ガイドの「日々の開発の流れ」</a>へ。物理作業中心のIssueでも、<code>robot/</code> のコードや記録を変更するときは同じ流れで。</p>
  </div>

${body}

  <footer>
    <p><a class="top-link" href="index.html">← 開発の手引き トップに戻る</a></p>
    <p>移動本棚ロボ｜開発の手引き ／ 内容が古くなっていたら気づいた人が直して PR を。</p>
  </footer>

</div>
</body>
</html>
`;
}

function cardOf(d) {
  const cls = CARD_CLASS[d.category] || "";
  const no = (typeof d.issueNo === "number") ? `ISSUE #${d.issueNo}` : esc(d.category);
  const tags = [];
  if (d.screen) tags.push(`<span class="pill">${esc(d.screen)}</span>`);
  if (d.role) tags.push(`<span class="pill role">${esc(d.role)}</span>`);
  if (d.status) tags.push(`<span class="pill done">${esc(d.status)}</span>`);
  else if (d.sprint) tags.push(`<span class="pill sprint">${esc(d.sprint)}</span>`);
  return `<a class="card ${cls}" href="${fileNameOf(d)}">
      <div class="c-no">${no}</div>
      <div class="c-title">${esc(d.taskName)}</div>
      <div class="c-desc">${inline(d.oneLineGoal)}</div>
      <div class="c-tags">${tags.join("")}</div>
    </a>`;
}

// グループの表示順と絵文字
const GROUP_ORDER = ["アプリ（追加機能）", "ロボ側ソフト", "ハードウェア設計", "結合・品質・公開", "COULD（余力で）"];
const GROUP_EMOJI = {
  "アプリ（追加機能）": "💻", "ロボ側ソフト": "🤖", "ハードウェア設計": "🔧",
  "結合・品質・公開": "🚀", "COULD（余力で）": "✨",
};

function renderIndex(numbered, extras) {
  const envCard = `<a class="card env" href="環境構築ガイド.html">
      <div class="c-no">まず最初に</div>
      <div class="c-title">🛠 環境構築ガイド</div>
      <div class="c-desc">アプリを自分のPCで動かせるようにする手順（clone → npm install → .env.local → npm run dev）。新メンバーは最初にここ。</div>
      <div class="c-tags"><span class="pill">全員</span><span class="pill sprint">Sprint 0</span></div>
    </a>`;

  const numberedCards = numbered.map(cardOf).join("\n    ");

  // グループごとにまとめる
  const byGroup = {};
  for (const d of extras) (byGroup[d.group] = byGroup[d.group] || []).push(d);
  Object.values(byGroup).forEach(arr => arr.sort((a, b) => (a.order || 0) - (b.order || 0)));
  const groupNames = GROUP_ORDER.filter(g => byGroup[g]).concat(
    Object.keys(byGroup).filter(g => !GROUP_ORDER.includes(g)));

  const extraSections = groupNames.map(g => {
    const cards = byGroup[g].map(cardOf).join("\n    ");
    const emoji = GROUP_EMOJI[g] || "📄";
    return `  <section>
    <div class="group-head"><span class="g-emoji">${emoji}</span><h2 class="sec" style="border:none;margin:0;padding:0">${esc(g)}</h2><span class="g-count">${byGroup[g].length}件</span></div>
    <div class="cards">
      ${cards}
    </div>
  </section>`;
  }).join("\n\n");

  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>開発の手引き｜移動本棚ロボ</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="wrap">

  <header class="hero">
    <h1>📚 開発の手引き（困ったときはここ）</h1>
    <p>移動本棚ロボ アプリの開発で迷ったら、まずこのフォルダを開いてください。<br>
    環境構築から、各Issueの進め方・調べ方、ロボ・ハード・公開までをまとめています。</p>
    <div class="badges">
      <span class="badge">🎓 初心者向け</span>
      <span class="badge">🧭 進め方＋調べ方</span>
      <span class="badge">🤖 AIコーチの使い方つき</span>
      <span class="badge">📦 全Issue網羅</span>
    </div>
  </header>

  <div class="callout tip">
    <p><strong>使い方</strong>：自分の担当 Issue のカードを開くと、「やること → 進め方の手順 → <strong>進めながら調べる・学ぶこと</strong> → つまずき対処 → 動作確認 → AIコーチへの頼み方」がそろっています。コードの答えは載せていません（自分で書いて学ぶスタイル）。</p>
  </div>

  <section>
    <h2 class="sec">まず最初に</h2>
    <div class="cards">
      ${envCard}
    </div>
  </section>

  <section>
    <h2 class="sec">最初のスプリント：Issue #1〜#9</h2>
    <p class="section-intro">担当の色：<span class="pill role">アプリ</span> <span class="pill robot" style="background:#f3e8ff;border-color:#e9d5ff;color:#6b21a8">ロボ</span> <span class="pill done">基盤（PM・完了済み）</span>。番号は GitHub の実Issue番号です。</p>
    <div class="cards">
      ${numberedCards}
    </div>
  </section>

  <section>
    <h2 class="sec">その先のすべてのIssue（カテゴリ別）</h2>
    <p class="section-intro">#1〜#9 の後に取り組む Issue 一式。まだ GitHub 番号は振られていないものが多いので、<strong>タスク名・画面名(S-◯)・要件(F-◯/C-◯)で照合</strong>してください。</p>
  </section>

${extraSections}

  <section>
    <h2 class="sec">着手のおすすめ順 ＆ 依存関係</h2>
<pre class="ascii">[完了] #2 テーブル作成 / #4 リポジトリ雛形 ─┐
   #3 サンプルデータ投入（PM）───────────────┤
   アプリ：#1 本一覧 → #5 本登録 / #6 ゲスト名 → #7 借りる/返す → S-4 呼出 → S-5 履歴 / 現在地表示
   ロボ ：#8 ブリッジ最小 → ブリッジ完全版 →（#9 座標変換 → Nav2自走）/ LED / 切断時の安全停止
   ハード：Pi5 → 筐体 / モーター / 本棚+NeoPixel / 回路設計(BB→KiCad) → 実機マッピング
                          ↓（アプリとロボが各自そろったら）
   結合テスト → 受入テスト → Vercel公開 → 発表準備</pre>
    <div class="callout tip"><p>最初の山場は <strong>#1 本一覧（S-1）</strong>。「Supabaseから読んで画面に出す」最初の成功体験で、ここを越えると他の画面は同じ型で書けます。MVPの本命ラインは #1・#5・#7・S-4・現在地表示／#8・ブリッジ完全版・Nav2自走・LED／Pi5・筐体・本棚+LED・マッピング／結合テスト。</p></div>
  </section>

  <section>
    <h2 class="sec">知っておくと混乱しないこと</h2>
    <div class="callout warn">
      <span class="label">① 資料によって Issue 番号がズレています</span>
      <p>この手引きは「コピペ用Issue集（GitHub実番号版）」に合わせ、<strong>#1＝本一覧</strong> としています。一部の古い資料では <strong>#4＝本一覧</strong>（旧番号）で書かれています。迷ったら<strong>番号より「タスク名・画面名(S-◯)」で照合</strong>してください。</p>
    </div>
    <div class="callout danger">
      <span class="label">② 接続キーの名前（重要）</span>
      <p>一部の古い資料に <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code> や <code>.env.local.example</code> という記述がありますが、<strong>現在のリポジトリは <code>NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY</code> に統一</strong>され、example ファイルは廃止しました。<a href="環境構築ガイド.html">環境構築ガイド</a>の手順が正です。</p>
    </div>
    <table>
      <tr><th>記号</th><th>意味</th><th>例</th></tr>
      <tr><td><strong>S-◯</strong></td><td>画面番号（Screen）。画面のIssueにだけ付く</td><td>S-1 本一覧</td></tr>
      <tr><td><strong>F-◯</strong></td><td>機能要件（Function）</td><td>F-08 ロボ呼出</td></tr>
      <tr><td><strong>C-◯</strong></td><td>任意機能（Could）</td><td>C-01 検索</td></tr>
      <tr><td><strong>#◯</strong></td><td>GitHubのIssue番号（自動採番。S-とは別物）</td><td>#1</td></tr>
    </table>
  </section>

  <footer>
    <p>移動本棚ロボ｜開発の手引き ／ ものづくりゼミ 2026（9人・3ヶ月）</p>
    <p>この手引きは生きた資料です。分かりにくい所・古い所は、気づいた人が直して PR を出してください。</p>
  </footer>

</div>
</body>
</html>
`;
}

// ---- 読み込み ----
function loadDir(dir, pattern, required) {
  if (!fs.existsSync(dir)) return [];
  const files = fs.readdirSync(dir).filter(f => pattern.test(f));
  const out = [];
  for (const f of files) {
    const raw = fs.readFileSync(path.join(dir, f), "utf8");
    let d;
    try { d = JSON.parse(raw); }
    catch (e) { console.error(`JSON parse error in ${f}: ${e.message}`); process.exit(1); }
    for (const k of required) {
      if (d[k] == null) { console.error(`Missing field "${k}" in ${f}`); process.exit(1); }
    }
    out.push(d);
  }
  return out;
}

const numbered = loadDir(SRC, /^issue-\d+\.json$/, ["issueNo", "taskName", "oneLineGoal", "sections"]);
numbered.sort((a, b) => a.issueNo - b.issueNo);

const extras = loadDir(SRC2, /\.json$/, ["id", "group", "taskName", "oneLineGoal", "sections"]);

// ---- 書き出し ----
for (const d of numbered) {
  fs.writeFileSync(path.join(DIR, fileNameOf(d)), renderItem(d), "utf8");
  console.log(`wrote ${fileNameOf(d)}  (#${d.issueNo} ${d.taskName})`);
}
for (const d of extras) {
  fs.writeFileSync(path.join(DIR, fileNameOf(d)), renderItem(d), "utf8");
  console.log(`wrote ${fileNameOf(d)}  (${d.group} / ${d.taskName})`);
}
fs.writeFileSync(path.join(DIR, "index.html"), renderIndex(numbered, extras), "utf8");
console.log(`wrote index.html  (numbered=${numbered.length}, extras=${extras.length})`);
