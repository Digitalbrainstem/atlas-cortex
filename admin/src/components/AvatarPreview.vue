<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'

const props = defineProps({
  skinId: { type: String, default: 'default' },
  size: { type: Number, default: 200 },
  animate: { type: Boolean, default: true },
})

const container = ref(null)
let animTimer = null
let blinkTimer = null

const VISEMES = ['IDLE','PP','FF','TH','DD','KK','SS','SH','RR','NN','IH','EH','AA','OH','OU']
const EXPRESSIONS = ['neutral','happy','thinking','surprised','sad','excited','concerned','listening']

async function loadSkin() {
  if (!container.value) return
  try {
    const resp = await fetch(`/avatar/skin/${props.skinId}.svg`)
    if (!resp.ok) return
    container.value.innerHTML = await resp.text()
    const svg = container.value.querySelector('svg')
    if (svg) {
      svg.style.width = props.size + 'px'
      svg.style.height = props.size + 'px'
    }
    showViseme('IDLE')
    showExpression('neutral')
    if (props.animate) startDemo()
  } catch (e) {
    console.error('Failed to load skin:', e)
  }
}

function showViseme(viseme) {
  VISEMES.forEach(v => {
    const el = container.value?.querySelector(`#mouth-${v}`)
    if (el) el.style.display = (v === viseme) ? 'inline' : 'none'
  })
}

function showExpression(expr) {
  EXPRESSIONS.forEach(e => {
    const el = container.value?.querySelector(`#expr-${e}`)
    if (el) el.style.display = 'none'
  })
  const el = container.value?.querySelector(`#expr-${expr}`)
  if (el) el.style.display = 'inline'
  const baseEyes = container.value?.querySelector('#eyes-base')
  const hasOwnEyes = el && el.querySelector('ellipse')
  if (baseEyes) {
    baseEyes.style.display = (expr === 'neutral' || !hasOwnEyes) ? 'inline' : 'none'
  }
}

function startDemo() {
  const sequence = ['IDLE','PP','AA','EH','OH','PP','IH','DD','SS','IDLE']
  let i = 0
  animTimer = setInterval(() => {
    showViseme(sequence[i % sequence.length])
    i++
    if (i >= sequence.length) {
      i = 0
      // Cycle expression
      const expr = EXPRESSIONS[Math.floor(Math.random() * EXPRESSIONS.length)]
      showExpression(expr)
    }
  }, 200)
  blinkTimer = setInterval(() => {
    const blink = container.value?.querySelector('#blink')
    const baseEyes = container.value?.querySelector('#eyes-base')
    if (!blink) return
    blink.style.display = 'inline'
    if (baseEyes) baseEyes.style.opacity = '0'
    setTimeout(() => {
      blink.style.display = 'none'
      if (baseEyes) baseEyes.style.opacity = '1'
    }, 120)
  }, 4000)
}

function stopDemo() {
  if (animTimer) { clearInterval(animTimer); animTimer = null }
  if (blinkTimer) { clearInterval(blinkTimer); blinkTimer = null }
}

watch(() => props.skinId, () => { stopDemo(); loadSkin() })
onMounted(loadSkin)
onUnmounted(stopDemo)
</script>

<template>
  <div ref="container" class="avatar-preview" :style="{ width: size + 'px', height: size + 'px' }"></div>
</template>

<style scoped>
.avatar-preview {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #1e293b;
  border-radius: 12px;
  overflow: hidden;
}
.avatar-preview :deep(svg) {
  display: block;
}
.avatar-preview :deep(g) {
  transition: opacity 0.12s ease;
}
</style>
