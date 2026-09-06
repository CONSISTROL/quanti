import { ref, watch } from 'vue'

const PREFIX = 'quanti.ui.'

export function usePersistentRef(key, defaultValue) {
  const storageKey = PREFIX + key
  let initial = defaultValue
  try {
    const raw = localStorage.getItem(storageKey)
    if (raw !== null) {
      initial = JSON.parse(raw)
    }
  } catch (e) {
    // ignore invalid storage
  }

  const value = ref(initial)

  watch(
    value,
    (v) => {
      try {
        localStorage.setItem(storageKey, JSON.stringify(v))
      } catch (e) {
        // ignore quota/security errors
      }
    },
    { deep: true }
  )

  return value
}
