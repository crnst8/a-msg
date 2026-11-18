import { useEffect, useRef, useState } from 'react'
import { useNavigation, type Overlay, type Route } from '@/app/navigation'
import { Symbol } from '@/components/Symbol'
import { Button, inputClass } from '@/components/ui'
import { usePreferences } from '@/state/preferences'
import { useSpaces } from '@/state/queries'
import styles from './Help.module.css'

interface Action {
  label: string
  route?: Route
  overlay?: Overlay
}

interface Topic {
  id: string
  title: string
  description: string
  steps: string[]
  tip?: string
  action?: Action
}

export function Help() {
  const { navigate, show } = useNavigation()
  const spaces = useSpaces()
  const prefs = usePreferences()
  const heading = useRef<HTMLHeadingElement>(null)
  const [query, setQuery] = useState('')
  const general = spaces.find((space) => space.kind === 'general')
  const command = navigator.userAgent.includes('Macintosh') ? '⌘' : 'Ctrl'

  useEffect(() => heading.current?.focus({ preventScroll: true }), [])

  const topics: Topic[] = [
    {
      id: 'chat', title: 'Send a message or stop a reply',
      description: 'Talk to Hermes in General, or pick up a conversation in a space.',
      steps: [
        'Open General or choose a space in the navigation rail. Type in the message box and select Send.',
        prefs.enterSends
          ? 'Enter sends your message. Shift + Enter adds a new line.'
          : `${command} + Enter sends your message. Enter adds a new line.`,
        'Replies appear as the agent writes. Stop appears beside an empty message box when no attachments are waiting. Select it to end the reply.',
        'If a reply fails or is stopped, select Retry beneath it to try again.',
      ],
      tip: 'A useful request says what you want, gives the relevant context and describes the result. Try: “Compare these two files and give me the three differences that matter most.”',
      action: general ? { label: 'Open General', route: { view: 'space', id: general.id } } : undefined,
    },
    {
      id: 'spaces', title: 'Keep topics in separate spaces',
      description: 'Give a project or recurring task its own conversation.',
      steps: [
        'Select the + button in the navigation rail, choose a name and icon, then create the space.',
        'Select its icon to return to it. On a narrow screen, open the more-spaces arrow to find spaces that do not fit.',
        'Open Space options at the top of the conversation to rename it, change its icon or archive it. On a computer, you can also right-click its navigation icon.',
      ],
      tip: 'Drafts are saved per space on this device. You can leave an unfinished message and come back to it.',
      action: { label: 'Create a space', overlay: { kind: 'new-space' } },
    },
    {
      id: 'threads', title: 'Branch a conversation into a thread',
      description: 'Take a side discussion elsewhere without losing its starting point.',
      steps: [
        'Hover over a message, or tap it on a phone, to reveal its tools.',
        'Open More and choose Create thread from here. Select the messages you want to take with you, then choose Create thread.',
        'Name the thread and create it. The new conversation keeps a link to its source.',
      ],
    },
    {
      id: 'files', title: 'Attach, find and save files',
      description: 'Send context to an agent and keep the files it gives you.',
      steps: [
        'Select the paperclip beside the message box and choose your files. On a computer, you can also drag files into the conversation or paste an image.',
        'Wait for uploads to finish, add your instructions and select Send. Remove an attachment with its × button; retry a failed upload with its retry button.',
        'Open Library to find files across conversations. Search by file name, or filter by file type and source space.',
        'Select a file to preview it. Use Save on a message attachment, or Save in the Library preview, to keep a copy.',
      ],
      action: { label: 'Open Library', route: { view: 'library' } },
    },
    {
      id: 'search', title: 'Find an old message, file or space',
      description: 'Jump straight to something you have already worked on.',
      steps: [
        `Press ${command} + K to open Search. Type a space name, words from a message or a file name.`,
        'Select a result to open it. On a keyboard, use the arrow keys and Enter.',
        `To search just the current conversation, press ${command} + F. In a space, you can also open Space options and choose Find in conversation.`,
      ],
      action: { label: 'Open Search', overlay: { kind: 'palette' } },
    },
    {
      id: 'code', title: 'Start a coding session',
      description: 'Work with a coding agent in a specific folder.',
      steps: [
        'Open Code Connector. If your agent is not listed, choose Add connection and enter its details and allowed folders.',
        'Choose New session on a connection. Enter the working directory on that agent’s machine and give the session a name.',
        'If Access is offered, choose Read only for inspection, Workspace for changes in the working folder or Full access for broader access. Available choices depend on the connection.',
        'Start the session and send your task. Return to it from Code Connector.',
      ],
      tip: 'Use a clear first request: “Find why this test fails, explain the cause, then fix it and run the relevant tests.”',
      action: { label: 'Open Code Connector', route: { view: 'code' } },
    },
    {
      id: 'automations', title: 'Set up and manage recurring tasks',
      description: 'Ask Hermes to do something on a schedule.',
      steps: [
        'Ask Hermes for a recurring task. Include what to do, when to do it, the timezone and where the result should be posted.',
        'For example: “Every weekday at 8 am London time, post my daily briefing in this space.” Check the schedule Hermes confirms.',
        'A clock badge marks a space with an automation. Open that space and select its automation control to see the schedule, timezone and next run.',
        'Use Pause or Resume to control the schedule, Run now for an extra run, or Destination to change where results go.',
      ],
      tip: 'If the automation says its status may be out of date, the scheduler has not reported recently. Check the connection before relying on the next run.',
    },
    {
      id: 'offline', title: 'Understand queued messages and connection problems',
      description: 'Know whether your message is waiting or needs attention.',
      steps: [
        'A message can be Queued while another reply finishes, or while the server is unreachable. It sends when the connection and agent are ready.',
        'Use Edit or Remove on a queued message to change it before it sends.',
        'If the app stays Offline or Reconnecting, check your network, then open Settings → Connections to inspect the server status.',
        'For a coding agent, open its options in Code Connector and choose Test connection. The results show what needs attention.',
      ],
      tip: 'Avoid signing out to fix a temporary outage. Signing back in needs your login details or a new desktop pairing code.',
      action: { label: 'Check Connections', route: { view: 'settings', tab: 'connections' } },
    },
    {
      id: 'archive', title: 'Archive a space or bring it back',
      description: 'Clear the navigation rail while keeping the conversation.',
      steps: [
        'Open a space’s Space options and choose Archive. On a computer, you can also right-click its navigation icon.',
        'Select Undo in the confirmation toast if you archived it by mistake.',
        'Later, open Settings → Connections → Archived spaces, then restore the space you want.',
      ],
      tip: 'Archive keeps a conversation for later. Delete is a separate action with a confirmation step.',
      action: { label: 'View archived spaces', overlay: { kind: 'archive' } },
    },
    {
      id: 'appearance', title: 'Make the app comfortable to use',
      description: 'Adjust text, colors, navigation and typing behavior.',
      steps: [
        'Open Settings → Appearance to choose light, dark or system mode, a palette, font, text size and density.',
        'On a computer, choose a rail position and turn on Show labels if you prefer names beside the icons. On a phone, navigation stays at the bottom.',
        'Change Enter sends to choose whether Enter sends a message or adds a new line. You can always use the Send button.',
        'Use Settings → Notifications to choose which background completions, failures and replies should notify you.',
      ],
      action: { label: 'Open Appearance', route: { view: 'settings', tab: 'appearance' } },
    },
    {
      id: 'phone', title: 'Use a-msg on your phone',
      description: 'The same conversations, with controls that fit a smaller screen.',
      steps: [
        'Use the bottom navigation to switch views. Help is beside Settings; the more-spaces arrow reveals spaces that do not fit.',
        'Tap a message to reveal Copy and More. Use the paperclip to choose a photo or file.',
        'The bottom navigation hides while the on-screen keyboard is open. Dismiss the keyboard to switch views.',
        'On an iPhone, open the site in Safari, then use Share → Add to Home Screen for an app-style window.',
      ],
    },
  ]
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
  const searching = words.length > 0
  const matches = topics.filter((topic) => {
    const text = [topic.title, topic.description, ...topic.steps, topic.tip ?? ''].join(' ').toLowerCase()
    return words.every((word) => text.includes(word))
  })
  const relevance = (topic: Topic) => words.reduce((score, word) => score
    + (topic.title.toLowerCase().includes(word) ? 10 : 0)
    + (topic.description.toLowerCase().includes(word) ? 3 : 0), 0)
  if (searching) matches.sort((a, b) => relevance(b) - relevance(a))
  const run = (action: Action) => {
    if (action.route) navigate(action.route)
    else if (action.overlay) show(action.overlay)
  }

  return (
    <div className={styles.view}>
      <div className={styles.column}>
        <header className={styles.header}>
          <p className={styles.eyebrow}>A little guidance</p>
          <h1 ref={heading} tabIndex={-1} className={styles.title}>Help</h1>
          <p className={styles.intro}>Find your way around. Get something done.</p>
        </header>

        {!searching && (
          <section className={styles.start} aria-labelledby="help-start">
            <h2 id="help-start">Start here</h2>
            <ol className={styles.quickSteps}>
              <li><strong>Ask in General</strong><p>Tell Hermes what you need and what a good result looks like.</p></li>
              <li><strong>Give it context</strong><p>Attach a file, share the details or explain the constraints.</p></li>
              <li><strong>Keep it organized</strong><p>Use a separate space for each project. Find shared files in Library.</p></li>
            </ol>
            {general && <Button variant="outline" onClick={() => navigate({ view: 'space', id: general.id })}>Go to General <span aria-hidden="true">→</span></Button>}
          </section>
        )}

        <section className={styles.topics} aria-labelledby="help-topics">
          <h2 id="help-topics">How do I…</h2>
          <div className={styles.search}>
            <Symbol name="search" size={18} />
            <input className={`${inputClass} ${styles.searchInput}`} type="search" aria-label="Search help" placeholder="Search help — try files, schedule or offline" value={query} onChange={(event) => setQuery(event.target.value)} />
          </div>
          <p className={styles.resultCount} role="status">{searching ? `${matches.length} ${matches.length === 1 ? 'topic' : 'topics'} found` : 'Choose a topic for the steps. Open as many as you need.'}</p>
          <div className={styles.answers}>
            {matches.map((topic) => (
              <details key={`${topic.id}-${searching}`} className={styles.topic} open={searching || undefined}>
                <summary><span><span className={styles.topicTitle}>{topic.title}</span><span className={styles.description}>{topic.description}</span></span></summary>
                <div className={styles.answer}>
                  <ol>{topic.steps.map((step) => <li key={step}>{step}</li>)}</ol>
                  {topic.tip && <p className={styles.tip}>{topic.tip}</p>}
                  {topic.action && <Button variant="outline" onClick={() => run(topic.action!)}>{topic.action.label} <span aria-hidden="true">→</span></Button>}
                </div>
              </details>
            ))}
          </div>
          {matches.length === 0 && <div className={styles.empty}><p>No matching topics. Try “message”, “files” or “connection”.</p><Button variant="outline" onClick={() => setQuery('')}>Show all topics</Button></div>}
        </section>

        <footer className={styles.footer}>
          <span>Handy on a keyboard</span>
          <span><kbd>{command} K</kbd> Search</span><span><kbd>{command} N</kbd> New space</span><span><kbd>{command} ,</kbd> Settings</span>
        </footer>
      </div>
    </div>
  )
}
