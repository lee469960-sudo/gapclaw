import assert from 'node:assert/strict'
import test from 'node:test'

import { assistantVisibleText, messagesAfterPoll } from './assistantDisplay.js'

test('old assistant messages render their original content', () => {
  const text = assistantVisibleText({
    role: 'assistant',
    content: '这是变更前保存的正文。',
  })
  assert.equal(text, '这是变更前保存的正文。')
  assert.equal(text.includes('version'), false)
})

test('a partial reply renders the envelope message, not the JSON', () => {
  const message = '已停止。已做到订单查询，汇总尚未完成。'
  const text = assistantVisibleText({
    role: 'assistant',
    content: message,
    meta: {
      output: {
        version: '1.0',
        status: 'partial',
        type: 'answer',
        message,
        data: {},
        actions: [],
      },
    },
  })
  assert.equal(text, message)
  assert.equal(text.includes('"status"'), false)
  assert.equal(text.includes('unfinished_alpha'), false)
})

test('an in-progress poll does not replace the existing assistant bubble', () => {
  const current = [
    { id: 'u1', role: 'user', content: '继续' },
    { id: 'a1', role: 'assistant', content: '上一轮的结论。' },
  ]
  const incoming = [
    { id: 'u1', role: 'user', content: '继续' },
    { id: 'a1', role: 'assistant', content: '被轮询改写的正文' },
    { id: 'a2', role: 'assistant', content: 'def unfinished_alpha():\n    return 1' },
  ]
  const shown = messagesAfterPoll(true, current, incoming)
  assert.deepEqual(shown.map((item) => item.id), ['u1', 'a1'])
  assert.equal(shown[1].content, '上一轮的结论。')
  assert.equal(JSON.stringify(shown).includes('unfinished_alpha'), false)
})

test('a finished poll can show the new assistant reply', () => {
  const current = [{ id: 'u1', role: 'user', content: '继续' }]
  const incoming = [
    { id: 'u1', role: 'user', content: '继续' },
    { id: 'a2', role: 'assistant', content: '已完成。' },
  ]
  const shown = messagesAfterPoll(false, current, incoming)
  assert.equal(shown.at(-1).content, '已完成。')
})
