import bash from 'highlight.js/lib/languages/bash'
import css from 'highlight.js/lib/languages/css'
import diff from 'highlight.js/lib/languages/diff'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'
import { memo, useRef, useState, type ComponentProps } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import remarkGfm from 'remark-gfm'
import { IconButton } from '@/components/ui'
import { useApp } from '@/state/queries'
import styles from './Markdown.module.css'

const languages = {
  bash, css, diff, javascript, json, markdown, python, rust, sql, typescript, xml, yaml,
  sh: bash, shell: bash, js: javascript, ts: typescript, py: python, yml: yaml, html: xml, rs: rust,
}
const remarkPlugins = [remarkGfm]
const highlighted = [[rehypeHighlight, { languages, detect: false, plainText: ['text', 'txt'] }]] as never
const plain: never[] = []

const SAFE = /^(https?:|mailto:)/i

/** Anything not http, https or mailto loses its target and stays readable as text. */
function safeUrl(url: string): string {
  return SAFE.test(url.trim()) ? url : ''
}

function Link({ href, children }: ComponentProps<'a'>) {
  const { transport } = useApp()
  if (!href) return <span>{children}</span>
  return (
    <a
      href={href}
      rel="noreferrer"
      onClick={(event) => {
        event.preventDefault()
        void transport.openExternal(href)
      }}
    >
      {children}
    </a>
  )
}

/** Remote images would announce the reader to their host, so they appear as ordinary links. */
function RemoteImage({ src, alt }: ComponentProps<'img'>) {
  return typeof src === 'string' && src ? <Link href={src}>{alt || src}</Link> : <span>{alt}</span>
}

function Code({ children }: ComponentProps<'pre'>) {
  const ref = useRef<HTMLPreElement>(null)
  const [copied, setCopied] = useState(false)
  return (
    <div className={styles.code}>
      <pre ref={ref} tabIndex={0}>
        {children}
      </pre>
      <IconButton
        className={styles.copy}
        icon={copied ? 'check' : 'copy'}
        label={copied ? 'Copied' : 'Copy code'}
        onClick={() => {
          void navigator.clipboard.writeText(ref.current?.innerText ?? '')
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}
      />
    </div>
  )
}

function Table(props: ComponentProps<'table'>) {
  return (
    <div className={styles.tableScroll} tabIndex={0}>
      <table {...props} />
    </div>
  )
}

const components = { a: Link, img: RemoteImage, pre: Code, table: Table }

export const Markdown = memo(function Markdown({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <div className={styles.markdown}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        // The highlighter registers every language each time it is attached, so prose skips it.
        rehypePlugins={streaming || !text.includes('```') ? plain : highlighted}
        components={components as never}
        urlTransform={safeUrl}
        skipHtml
      >
        {text}
      </ReactMarkdown>
    </div>
  )
})
