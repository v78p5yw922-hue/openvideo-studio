/**
 * /create/music —— 音乐生成（MiniMax Music 3，本地）
 *
 * 新增：风格描述 / 歌词 的「?」帮助（结构、关键词、格式、常见坑）+ 一键填入示例。
 */

import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { t, tpl } from '../lib/i18n'
import { toast, useStore } from '../lib/store'
import { Modal } from '../components/Modal'
import { AudioDetail } from '../components/AudioDetail'
import { AudioBar } from '../lib/audioPlayer'
import './MusicPage.css'

type Cand = { id: string; kind: string; prompt: string; seed: number; url?: string; path?: string }
type Job = { id: string; state: string; progress?: { pct?: number | null; detail?: string } }

const PRESETS = [
  // —— 中式
  { id: 'cn_folk', label: '中式·民谣', zh: '木吉他 + 二胡，温暖、怀旧，中慢速',
    caption: 'Chinese folk ballad, acoustic guitar and erhu, warm and nostalgic, mid tempo 80 BPM, no vocals' },
  { id: 'cn_guofeng', label: '中式·国风', zh: '古筝 + 竹笛 + 轻鼓，清亮、悠远',
    caption: 'Chinese guofeng, guzheng and bamboo flute with light drums, bright and airy, mid tempo, instrumental' },
  { id: 'cn_xiqu', label: '中式·戏曲元素', zh: '京胡、板鼓、锣，戏曲腔的顿挫',
    caption: 'Chinese opera-flavoured score, jinghu fiddle, woodblock and gong, dramatic accents, mid tempo, instrumental' },
  // —— 西式
  { id: 'west_piano', label: '西式·钢琴', zh: '钢琴独奏 + 弦乐垫底，忧郁、克制',
    caption: 'soft solo piano with warm strings pad, melancholic and restrained, slow tempo 62 BPM, instrumental' },
  { id: 'west_jazz', label: '西式·爵士', zh: '钢琴三重奏，刷鼓、走低音，夜晚酒吧感',
    caption: 'jazz piano trio, brushed drums and walking bass, smoky late-night club feel, mid tempo 96 BPM, instrumental' },
  { id: 'west_orch', label: '西式·管弦', zh: '弦乐 + 铜管，开阔、有 ascend 的推进',
    caption: 'cinematic orchestral score, strings and french horn, wide and rising, slow build, instrumental' },
  // —— 中国城市 / 地域
  { id: 'city_beijing', label: '北京·胡同', zh: '京腔民谣，三弦 + 口琴，灰砖院墙的清晨',
    caption: 'Beijing hutong folk, sanxian and harmonica, grey brick courtyards at dawn, warm and plain, mid tempo 84 BPM' },
  { id: 'city_neimeng', label: '内蒙·草原', zh: '马头琴 + 长调式人声，草原与风',
    caption: 'Inner Mongolian grassland, morin khuur and throaty long-tone vocal, open and vast, slow tempo 60 BPM' },
  { id: 'city_shenzhen', label: '深圳·夜色', zh: '合成器 + 电鼓，霓虹、车流、都市夜',
    caption: 'Shenzhen city night, warm synth pads and electronic drums, neon and traffic, mid tempo 104 BPM, instrumental' },
  // —— 带人声（人声配方，旋律清楚）
  { id: 'vocal_folk', label: '女声民谣·雨天', zh: '中文女声 + 民谣吉他 + 二胡，咬字清楚',
    caption: 'Chinese folk ballad, acoustic guitar and erhu, warm female vocal singing in Mandarin, clear diction, close-mic, gentle and bittersweet, slow tempo 70 BPM, intimate room reverb' },
  { id: 'vocal_piano', label: '女声钢琴·旧照片', zh: '中文女声 + 钢琴 + 大提琴，克制、怀旧',
    caption: 'Soft solo piano with warm cello, female vocal singing in Mandarin, clear diction, close-mic, melancholic and tender, slow tempo 64 BPM, dry vocal, no reverb wash' },
  { id: 'vocal_summer', label: '女声轻快·夏天', zh: '中文女声 + 尤克里里，明亮轻快',
    caption: 'Upbeat acoustic folk, ukulele and light hand percussion, bright female vocal singing in Mandarin, clear diction, sunny and playful, mid tempo 108 BPM' },
]

const EXAMPLE_CAPTION =
  'Chinese folk ballad, acoustic guitar and erhu, warm female vocal singing in Mandarin, ' +
  'gentle and bittersweet, slow tempo 70 BPM, intimate room reverb'

const EXAMPLE_LYRICS = `[Verse]
窗外的雨下了一整天
桌上的水杯盛着天光

[Chorus]
我把第三次数了一遍
答案还在纸上`

const CAPTION_ROWS: Array<[string, string, string]> = [
  ['类型 / 流派', 'cinematic score · lo-fi hip hop · Chinese folk ballad · 1990s Mandopop · ambient', '先定这一条，其它都在它上面加'],
  ['主奏乐器', 'solo piano · acoustic guitar · erhu · guzheng · strings · brushed drums', '写 1–3 件，别堆超过 4 件'],
  ['情绪', 'melancholic · hopeful · tense · playful · bittersweet · calm', '和画面情绪对齐'],
  ['速度', 'slow tempo 60–70 BPM · mid tempo 90–110 · upbeat 120–130', '30 秒的段落建议 60–90'],
  ['人声', 'female vocal singing in Mandarin · male baritone · breathy · clear diction · no vocals', '要人声必须同时填歌词'],
  ['混音质感', 'intimate room reverb · wide stereo · lo-fi tape hiss · dry close-mic', '可选，写一个词就够'],
]

const LYRICS_RULES: Array<[string, string]> = [
  ['段落标签', '[Intro] [Verse] [Pre-Chorus] [Chorus] [Bridge] [Outro]，可重复写 [Verse 2]'],
  ['行长度', '一行一个乐句，中文 6–14 字；句子太长会被吞字'],
  ['段落配比', '30 秒 ≈ 1 主歌 + 1 副歌；60 秒 ≈ 2 主歌 + 副歌；再长加 [Bridge]'],
  ['纯器乐', '歌词留空（不要写「无」），caption 里写 no vocals / instrumental'],
  ['语言', '中文可以唱，但英文发音更稳；混写容易糊'],
  ['重复副歌', '同一段 [Chorus] 重复两次，记忆点更清楚'],
]

const PITFALLS: string[] = [
  '把歌词内容写进风格描述里 → 模型会当成风格词乱解，歌词请单独填',
  '风格描述超过 60 词 → 后半段基本不生效',
  '风格描述写中文 → 效果明显弱于英文',
  '勾了「带人声」但歌词是空的 → 大概率还是出器乐',
  '想要「一段到底不换风格」→ 加 same instruments throughout, no section change',
  '风格里写了 vocal 却没给歌词 → 模型会哼出不清晰的音 + 尾部噪声（滋滋声就是这么来的）',
  '歌词太稀疏（比如 4 行填 60 秒）→ 人声含糊、后半段靠噪声填满；每行约 5–7 秒最稳',
  '想让人声清楚 → 加 clear diction, close-mic, dry vocal, no reverb wash',
]

export function MusicPage() {
  const { pid } = useStore()
  const [caption, setCaption] = useState(PRESETS[0].caption)
  const [lyrics, setLyrics] = useState('')
  const [seconds, setSeconds] = useState(60)
  const [seed, setSeed] = useState(7)
  const [withVocal, setWithVocal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const [cands, setCands] = useState<Cand[]>([])
  const [help, setHelp] = useState<'caption' | 'lyrics' | 'pitfalls' | null>(null)
  const [detail, setDetail] = useState<Cand | null>(null)

  // 歌词行数（不算 [Verse] 这类标签）
  const lyricLines = lyrics.split(/\r?\n/).map((x) => x.trim())
    .filter((x) => x && !x.startsWith('[')).length
  const suggestSeconds = Math.max(30, Math.min(300, Math.round(lyricLines * 6 / 5) * 5))
  const wantsVocal = /vocal|sing|voice|唱|人声/i.test(caption)
  const mismatch = withVocal && lyricLines > 0
    && (seconds > lyricLines * 10 || seconds < lyricLines * 3)
  const timer = useRef<number | null>(null)

  const load = () => {
    api.assetCandidates()
      .then((r) => {
        const all = (r as unknown as { candidates: Cand[] }).candidates || []
        setCands(all.filter((c) => c.kind === 'music').slice(0, 20))
      })
      .catch(() => setCands([]))
  }

  useEffect(load, [])

  useEffect(() => {
    if (!job || job.state === 'done' || job.state === 'failed') return
    const tick = async () => {
      try {
        const j = await api.jobProgress(job.id) as unknown as Job
        setJob(j)
        if (j.state === 'done') { toast(t('music.done'), 'ok'); load() }
        if (j.state === 'failed') toast(t('music.failed'), 'err')
      } catch { /* 网络抖动，下轮再试 */ }
    }
    timer.current = window.setInterval(() => void tick(), 3000)
    return () => { if (timer.current) window.clearInterval(timer.current) }
  }, [job])

  const generate = async () => {
    if (!caption.trim()) { toast(t('music.need-caption'), 'err'); return }
    if (wantsVocal && (!withVocal || !lyrics.trim())) {
      toast(t('music.vocal-needs-lyrics'), 'err')
      return
    }
    if (withVocal && !lyrics.trim()) { toast(t('music.need-lyrics'), 'err'); return }
    setBusy(true)
    try {
      const zh = PRESETS.find((p) => p.caption === caption)?.zh || ''
      const r = await api.generateMusic({
        prompt: caption, caption_zh: zh, lyrics: withVocal ? lyrics : '',
        seconds, seed, project_id: pid || '',
      })
      setJob({ id: r.job_id, state: 'queued' })
      toast(tpl('music.queued', { n: r.queue_position }), 'ok')
    } catch (e) {
      toast(String(e), 'err')
    } finally {
      setBusy(false)
    }
  }

  const asBgm = async (id: string) => {
    if (!pid) { toast(t('music.pick-project'), 'err'); return }
    try {
      await api.candidateAsBgm(id, pid)
      toast(t('music.as-bgm-ok'), 'ok')
    } catch (e) {
      toast(String(e), 'err')
    }
  }

  return (
    <div className="page music-page">
      <div className="page-head">
        <div>
          <h1>{t('music.title')}</h1>
          <p className="muted">{t('music.subtitle')}</p>
        </div>
      </div>

      <div className="page-body music-body">
        <section className="card">
          <header className="card-head">
            <h2>{t('music.settings')}</h2>
            <button className="music-help" title={t('music.help.tip')}
                    onClick={() => setHelp('caption')}>?</button>
          </header>

          <div className="music-presets">
            {PRESETS.map((p) => (
              <button key={p.id} className={`music-chip${caption === p.caption ? ' is-active' : ''}`}
                      onClick={() => {
                        setCaption(p.caption)
                        if (/vocal|singing/i.test(p.caption)) setWithVocal(true)
                      }}>{p.label}</button>
            ))}
          </div>

          <label className="music-field">
            <span>
              {t('music.caption')}
              <button className="music-help sm" type="button"
                      onClick={() => setHelp('caption')}>?</button>
            </span>
            <textarea rows={3} value={caption} onChange={(e) => setCaption(e.target.value)}
                      placeholder={EXAMPLE_CAPTION} />
            <em className="muted music-hint">
              {PRESETS.find((p) => p.caption === caption)?.zh || t('music.caption.hint')}
            </em>
          </label>

          <label className="music-row">
            <input type="checkbox" checked={withVocal} onChange={(e) => setWithVocal(e.target.checked)} />
            <span>{t('music.with-vocal')}</span>
            <button className="music-help sm" type="button"
                    onClick={() => setHelp('lyrics')}>?</button>
          </label>

          {withVocal && (
            <label className="music-field">
              <span>{t('music.lyrics')}</span>
              <textarea rows={8} value={lyrics} onChange={(e) => setLyrics(e.target.value)}
                        placeholder={'[Verse]\n一行一句\n\n[Chorus]\n一句一句'} />
              <em className="muted music-hint">{t('music.lyrics.hint')}</em>
            </label>
          )}

          {seconds > 35 && (
            <p className="music-note">
              {t('music.long-hint')}
            </p>
          )}

          <div className="music-row">
            <label className="music-field">
              <span>{t('music.seconds')}</span>
              <select value={seconds} onChange={(e) => setSeconds(Number(e.target.value))}>
                {[30, 45, 60, 90, 120, 180, 240, 300].map((s) => (
                  <option key={s} value={s}>{s}s</option>
                ))}
              </select>
            </label>
            <label className="music-field">
              <span>{t('music.seed')}</span>
              <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
            </label>
          </div>

          {withVocal && lyricLines > 0 && (
            <p className={`music-note${mismatch ? ' is-warn' : ''}`}>
              {tpl('music.length-check', { n: lyricLines, s: suggestSeconds })}
              {mismatch && (
                <button className="ghost sm" onClick={() => setSeconds(suggestSeconds)}>
                  {tpl('music.use-length', { s: suggestSeconds })}
                </button>
              )}
            </p>
          )}
          {wantsVocal && (!withVocal || !lyrics.trim()) && (
            <p className="music-note is-warn">{t('music.vocal-needs-lyrics')}</p>
          )}

          <button className="btn" disabled={busy || (job ? !['done', 'failed'].includes(job.state) : false)}
                  onClick={() => void generate()}>
            {job && !['done', 'failed'].includes(job.state) ? t('music.generating') : t('music.generate')}
          </button>

          {job && !['done', 'failed'].includes(job.state) && (
            <div className="music-progress">
              <div className="bar"><i style={{ width: `${job.progress?.pct ?? 8}%` }} /></div>
              <span className="muted">{job.progress?.detail || t('music.generating')}</span>
            </div>
          )}
        </section>

        <section className="card">
          <header className="card-head"><h2>{t('music.library')}</h2>
            <span className="muted sm">{t('music.library-hint')}</span></header>

          {cands.length === 0 && <div className="muted">{t('music.empty')}</div>}
          <div className="music-list">
            {cands.map((c) => (
              <article key={c.id} className="music-item">
                <div className="music-meta">
                  <b>{c.prompt.slice(0, 60)}</b>
                  <span className="muted sm">seed {c.seed}</span>
                </div>
                {c.url && <AudioBar url={c.url} />}
                <div className="music-actions">
                  <button className="ghost sm" onClick={() => setDetail(c)}>{t('audio.detail')}</button>
                  <button className="ghost sm" onClick={() => void asBgm(c.id)}>{t('music.as-bgm')}</button>
                </div>
              </article>
            ))}
          </div>
          {!pid && <p className="muted sm">{t('music.pick-project')}</p>}
        </section>
      </div>

      {detail && <AudioDetail cand={detail as never} onClose={() => setDetail(null)} />}

      {help && (
        <Modal title={help === 'lyrics' ? t('music.help.lyrics-title')
                 : help === 'pitfalls' ? t('music.help.pitfalls-title') : t('music.help.caption-title')}
               onClose={() => setHelp(null)} wide>
          <div className="music-help-body">
            {help === 'caption' && (
              <>
                <p className="muted">{t('music.help.caption-lead')}</p>
                <table className="music-help-table">
                  <thead>
                    <tr><th>{t('music.help.col-item')}</th><th>{t('music.help.col-words')}</th>
                      <th>{t('music.help.col-note')}</th></tr>
                  </thead>
                  <tbody>
                    {CAPTION_ROWS.map(([k, v, n]) => (
                      <tr key={k}><td>{k}</td><td><code>{v}</code></td><td className="muted">{n}</td></tr>
                    ))}
                  </tbody>
                </table>
                <p className="muted">{t('music.help.caption-order')}</p>
                <pre className="music-help-code">{EXAMPLE_CAPTION}</pre>
                <button className="ghost sm" onClick={() => { setCaption(EXAMPLE_CAPTION); setHelp(null) }}>
                  {t('music.help.fill-caption')}
                </button>
              </>
            )}

            {help === 'lyrics' && (
              <>
                <p className="muted">{t('music.help.lyrics-lead')}</p>
                <table className="music-help-table">
                  <tbody>
                    {LYRICS_RULES.map(([k, v]) => (
                      <tr key={k}><td>{k}</td><td>{v}</td></tr>
                    ))}
                  </tbody>
                </table>
                <pre className="music-help-code">{EXAMPLE_LYRICS}</pre>
                <button className="ghost sm" onClick={() => {
                  setLyrics(EXAMPLE_LYRICS); setWithVocal(true); setHelp(null)
                }}>{t('music.help.fill-lyrics')}</button>
              </>
            )}

            {help === 'pitfalls' && (
              <ul className="music-help-list">
                {PITFALLS.map((p) => <li key={p}>{p}</li>)}
              </ul>
            )}
          </div>
        </Modal>
      )}
    </div>
  )
}
