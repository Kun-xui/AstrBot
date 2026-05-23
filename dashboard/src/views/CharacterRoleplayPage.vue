<template>
  <div class="dashboard-page character-roleplay-page" :class="{ 'is-dark': isDark }">
    <v-container fluid class="dashboard-shell pa-4 pa-md-6">
      <div class="dashboard-header">
        <div class="dashboard-header-main">
          <div class="d-flex align-center flex-wrap" style="gap: 8px;">
            <h1 class="dashboard-title">Character Roleplay</h1>
            <v-chip size="x-small" color="purple-darken-2" variant="tonal" label>
              Beta
            </v-chip>
          </div>
          <p class="dashboard-subtitle">
            Import and manage character packages for roleplay. Upload ZIP files to add new characters.
          </p>
        </div>

        <div class="dashboard-header-actions">
          <v-btn variant="text" color="primary" :loading="loading" prepend-icon="mdi-refresh" @click="loadCharacters">
            Refresh
          </v-btn>
          <v-btn variant="tonal" color="primary" prepend-icon="mdi-upload" :loading="uploading" @click="triggerUpload">
            Upload
          </v-btn>
          <input
            ref="fileInput"
            type="file"
            accept=".zip"
            style="display: none;"
            @change="handleUpload"
          />
        </div>
      </div>

      <div class="dashboard-section-head">
        <div>
          <div class="dashboard-section-title">Character Packages</div>
          <div class="dashboard-section-subtitle">
            {{ characters.length ? `${characters.length} character(s) imported` : 'No characters imported yet' }}
          </div>
        </div>
      </div>

      <section v-if="loading && !characters.length" class="state-panel">
        <v-progress-circular indeterminate size="22" width="2" color="primary" />
        <span>Loading characters...</span>
      </section>

      <section v-else-if="!characters.length" class="state-panel">
        <v-icon size="20" color="primary">mdi-account-group-outline</v-icon>
        <span>No character packages yet. Upload a ZIP to get started.</span>
      </section>

      <section v-else class="character-grid">
        <v-card
          v-for="char in characters"
          :key="char.id"
          class="character-card dashboard-card"
          :class="{ 'character-card--disabled': !char.enabled }"
          hover
          @click="openDetail(char)"
        >
          <div class="character-card-inner">
            <div class="character-card-avatar">
              <v-avatar size="56" rounded="12" color="primary" variant="tonal">
                <v-img v-if="char.avatar_url" :src="char.avatar_url" cover />
                <v-icon v-else size="28" color="primary">mdi-account-star</v-icon>
              </v-avatar>
            </div>
            <div class="character-card-info">
              <div class="character-card-name">{{ char.name || 'Unnamed' }}</div>
              <div class="character-card-source">{{ char.source_anime || 'Unknown Source' }}</div>
              <div class="character-card-badges">
                <v-chip
                  size="x-small"
                  :color="char.enabled ? 'success' : 'grey'"
                  variant="tonal"
                  label
                >
                  {{ char.enabled ? 'Enabled' : 'Disabled' }}
                </v-chip>
                <v-chip
                  size="x-small"
                  :color="char.tts_enabled ? 'info' : 'grey'"
                  variant="tonal"
                  label
                >
                  {{ char.tts_enabled ? 'TTS On' : 'TTS Off' }}
                </v-chip>
                <v-chip
                  size="x-small"
                  color="purple"
                  variant="tonal"
                  label
                >
                  {{ imageModeLabel(char.image_mode) }}
                </v-chip>
              </div>
            </div>
          </div>
        </v-card>
      </section>

      <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="2600">
        {{ snackbar.message }}
      </v-snackbar>

      <v-dialog v-model="detailDialog" max-width="780" scrollable>
        <v-card class="dashboard-dialog-card" v-if="selectedCharacter">
          <v-card-title class="text-h6 pt-5 px-5 d-flex align-center" style="gap: 12px;">
            <v-avatar size="36" rounded="8" color="primary" variant="tonal">
              <v-img v-if="selectedCharacter.avatar_url" :src="selectedCharacter.avatar_url" cover />
              <v-icon v-else size="20" color="primary">mdi-account-star</v-icon>
            </v-avatar>
            <span>{{ selectedCharacter.name || 'Unnamed Character' }}</span>
            <v-spacer />
            <v-switch
              v-model="selectedCharacter.enabled"
              inset
              density="compact"
              hide-details
              color="success"
              :label="selectedCharacter.enabled ? 'Enabled' : 'Disabled'"
              class="mt-0"
              @change="markDirty"
            />
          </v-card-title>

          <v-divider />

          <v-card-text class="px-5 pt-4 pb-2">
            <div class="dashboard-form-grid">
              <v-text-field
                v-model="selectedCharacter.name"
                label="Character Name"
                variant="outlined"
                density="comfortable"
                @input="markDirty"
              />
              <v-text-field
                v-model="selectedCharacter.source_anime"
                label="Source Anime / Game"
                variant="outlined"
                density="comfortable"
                @input="markDirty"
              />
            </div>

            <div class="detail-section-title mt-6 mb-2">System Prompt</div>
            <v-textarea
              v-model="selectedCharacter.system_prompt"
              variant="outlined"
              density="comfortable"
              rows="5"
              auto-grow
              placeholder="Enter the character's system prompt..."
              @input="markDirty"
            />

            <div class="detail-section-title mt-6 mb-2">Memory</div>
            <v-textarea
              v-model="selectedCharacter.memory"
              variant="outlined"
              density="comfortable"
              rows="4"
              auto-grow
              placeholder="Character memory context..."
              @input="markDirty"
            />

            <div class="detail-section-title mt-6 mb-3">TTS Settings</div>
            <div class="dashboard-form-grid">
              <v-switch
                v-model="selectedCharacter.tts_enabled"
                label="Enable TTS"
                inset
                color="info"
                hide-details
                @change="markDirty"
              />
              <div />
              <v-select
                v-model="selectedCharacter.tts_provider"
                :items="ttsProviders"
                label="TTS Provider"
                variant="outlined"
                density="comfortable"
                :disabled="!selectedCharacter.tts_enabled"
                @update:model-value="markDirty"
              />
              <v-text-field
                v-model="selectedCharacter.tts_voice"
                label="Voice"
                variant="outlined"
                density="comfortable"
                :disabled="!selectedCharacter.tts_enabled"
                @input="markDirty"
              />
            </div>

            <div class="detail-section-title mt-6 mb-3">Image Mode</div>
            <v-select
              v-model="selectedCharacter.image_mode"
              :items="imageModeOptions"
              item-title="label"
              item-value="value"
              label="Image Mode"
              variant="outlined"
              density="comfortable"
              @update:model-value="markDirty"
            />

            <div class="detail-section-title mt-6 mb-3">
              Image Gallery
              <span v-if="selectedCharacter.images?.length" class="detail-section-count">
                ({{ selectedCharacter.images.length }})
              </span>
            </div>
            <div v-if="selectedCharacter.images?.length" class="image-gallery">
              <div v-for="img in selectedCharacter.images" :key="img.filename" class="image-gallery-item">
                <v-img :src="img.url" cover class="image-gallery-thumb" />
                <div class="image-gallery-filename" :title="img.filename">{{ img.filename }}</div>
              </div>
            </div>
            <div v-else class="detail-empty-hint">No images available for this character.</div>
          </v-card-text>

          <v-divider />

          <v-card-actions class="px-5 pb-5 pt-3 d-flex flex-wrap" style="gap: 8px;">
            <v-btn
              variant="tonal"
              color="primary"
              :loading="saving"
              :disabled="!dirty"
              @click="saveCharacter"
            >
              Save Changes
            </v-btn>
            <v-btn
              variant="text"
              color="warning"
              @click="confirmCleanMemory"
            >
              Clean Memory
            </v-btn>
            <v-btn
              variant="text"
              color="info"
              @click="exportCharacter"
            >
              Export
            </v-btn>
            <v-spacer />
            <v-btn
              variant="text"
              color="error"
              @click="confirmDelete"
            >
              Delete
            </v-btn>
            <v-btn variant="text" @click="detailDialog = false">
              Close
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>

      <v-dialog v-model="confirmDialog.show" max-width="420">
        <v-card class="dashboard-dialog-card">
          <v-card-title class="text-h6 pt-5 px-5">{{ confirmDialog.title }}</v-card-title>
          <v-card-text class="px-5 pb-2 text-body-2">
            {{ confirmDialog.message }}
          </v-card-text>
          <v-card-actions class="justify-end px-5 pb-5">
            <v-btn variant="text" @click="confirmDialog.show = false">Cancel</v-btn>
            <v-btn
              variant="tonal"
              :color="confirmDialog.color"
              :loading="confirmDialog.loading"
              @click="confirmDialog.action"
            >
              {{ confirmDialog.confirmText }}
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>
    </v-container>
  </div>
</template>

<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref } from 'vue'
import { useTheme } from 'vuetify'

interface CharacterImage {
  filename: string
  url: string
}

interface Character {
  id: string
  name: string
  source_anime: string
  enabled: boolean
  tts_enabled: boolean
  tts_provider: string
  tts_voice: string
  image_mode: 'combined' | 'llm' | 'filename'
  system_prompt: string
  memory: string
  images: CharacterImage[]
  avatar_url?: string
}

const theme = useTheme()
const isDark = computed(() => theme.global.current.value.dark)

const loading = ref(false)
const uploading = ref(false)
const saving = ref(false)
const dirty = ref(false)
const characters = ref<Character[]>([])
const detailDialog = ref(false)
const selectedCharacter = ref<Character | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

const ttsProviders = ['edge-tts', 'openai', 'fish-audio', 'cosyvoice', 'chat-tts']
const imageModeOptions = [
  { label: 'Combined (Prompt + Image)', value: 'combined' },
  { label: 'LLM Generated', value: 'llm' },
  { label: 'Filename Match', value: 'filename' }
]

const snackbar = ref({ show: false, message: '', color: 'success' })

const confirmDialog = ref<{
  show: boolean
  title: string
  message: string
  confirmText: string
  color: 'error' | 'warning'
  loading: boolean
  action: () => void
}>({
  show: false,
  title: '',
  message: '',
  confirmText: 'Confirm',
  color: 'error',
  loading: false,
  action: () => {}
})

function toast(message: string, color: 'success' | 'error' | 'warning' = 'success') {
  snackbar.value = { show: true, message, color }
}

function imageModeLabel(mode: string): string {
  const map: Record<string, string> = {
    combined: 'Combined',
    llm: 'LLM',
    filename: 'Filename'
  }
  return map[mode] || mode
}

function markDirty() {
  dirty.value = true
}

async function loadCharacters() {
  loading.value = true
  try {
    const res = await axios.get('/api/character/list')
    if (res.data.status === 'ok') {
      characters.value = Array.isArray(res.data.data) ? res.data.data : []
    } else {
      toast(res.data.message || 'Failed to load characters', 'error')
    }
  } catch (e: any) {
    toast(e?.response?.data?.message || 'Failed to load characters', 'error')
  } finally {
    loading.value = false
  }
}

async function openDetail(char: Character) {
  try {
    const res = await axios.get(`/api/character/${char.id}`)
    if (res.data.status === 'ok') {
      selectedCharacter.value = { ...char, ...res.data.data }
    } else {
      selectedCharacter.value = { ...char }
    }
  } catch {
    selectedCharacter.value = { ...char }
  }
  dirty.value = false
  detailDialog.value = true
}

function triggerUpload() {
  fileInput.value?.click()
}

async function handleUpload(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return

  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', file)
    const res = await axios.post('/api/character/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    if (res.data.status === 'ok') {
      toast('Character package uploaded successfully')
      await loadCharacters()
    } else {
      toast(res.data.message || 'Upload failed', 'error')
    }
  } catch (e: any) {
    toast(e?.response?.data?.message || 'Upload failed', 'error')
  } finally {
    uploading.value = false
    if (target) target.value = ''
  }
}

async function saveCharacter() {
  if (!selectedCharacter.value) return
  saving.value = true
  try {
    const { id, images, avatar_url, ...payload } = selectedCharacter.value
    const res = await axios.put(`/api/character/${id}`, payload)
    if (res.data.status === 'ok') {
      toast('Character updated successfully')
      dirty.value = false
      await loadCharacters()
    } else {
      toast(res.data.message || 'Failed to update character', 'error')
    }
  } catch (e: any) {
    toast(e?.response?.data?.message || 'Failed to update character', 'error')
  } finally {
    saving.value = false
  }
}

function confirmDelete() {
  if (!selectedCharacter.value) return
  const charName = selectedCharacter.value.name || 'this character'
  confirmDialog.value = {
    show: true,
    title: 'Delete Character',
    message: `Are you sure you want to delete "${charName}"? This action cannot be undone.`,
    confirmText: 'Delete',
    color: 'error',
    loading: false,
    action: deleteCharacter
  }
}

async function deleteCharacter() {
  if (!selectedCharacter.value) return
  confirmDialog.value.loading = true
  try {
    const res = await axios.delete(`/api/character/${selectedCharacter.value.id}`)
    if (res.data.status === 'ok') {
      toast('Character deleted')
      detailDialog.value = false
      confirmDialog.value.show = false
      await loadCharacters()
    } else {
      toast(res.data.message || 'Failed to delete character', 'error')
    }
  } catch (e: any) {
    toast(e?.response?.data?.message || 'Failed to delete character', 'error')
  } finally {
    confirmDialog.value.loading = false
  }
}

function confirmCleanMemory() {
  if (!selectedCharacter.value) return
  confirmDialog.value = {
    show: true,
    title: 'Clean Memory',
    message: 'This will erase all stored memory for this character. Are you sure?',
    confirmText: 'Clean',
    color: 'warning',
    loading: false,
    action: cleanMemory
  }
}

async function cleanMemory() {
  if (!selectedCharacter.value) return
  confirmDialog.value.loading = true
  try {
    const res = await axios.post(`/api/character/${selectedCharacter.value.id}/clean-memory`)
    if (res.data.status === 'ok') {
      toast('Memory cleaned successfully')
      selectedCharacter.value.memory = ''
      dirty.value = true
      confirmDialog.value.show = false
    } else {
      toast(res.data.message || 'Failed to clean memory', 'error')
    }
  } catch (e: any) {
    toast(e?.response?.data?.message || 'Failed to clean memory', 'error')
  } finally {
    confirmDialog.value.loading = false
  }
}

async function exportCharacter() {
  if (!selectedCharacter.value) return
  try {
    const res = await axios.post(`/api/character/${selectedCharacter.value.id}/export`, {}, {
      responseType: 'blob'
    })
    const url = window.URL.createObjectURL(new Blob([res.data]))
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', `${selectedCharacter.value.name || 'character'}.zip`)
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)
    toast('Character exported')
  } catch (e: any) {
    toast(e?.response?.data?.message || 'Failed to export character', 'error')
  }
}

onMounted(() => {
  loadCharacters()
})
</script>

<style scoped>
@import '@/styles/dashboard-shell.css';

.character-roleplay-page {
  padding-bottom: 40px;
}

.character-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}

.character-card {
  cursor: pointer;
  transition: box-shadow 0.2s ease, transform 0.15s ease;
  border: 1px solid var(--dashboard-border);
  border-radius: 16px;
  background: var(--dashboard-surface);
}

.character-card:hover {
  box-shadow: 0 4px 20px rgba(var(--v-theme-primary), 0.12);
  transform: translateY(-1px);
}

.character-card--disabled {
  opacity: 0.6;
}

.character-card-inner {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 20px;
}

.character-card-avatar {
  flex-shrink: 0;
}

.character-card-info {
  min-width: 0;
  flex: 1;
}

.character-card-name {
  font-size: 15px;
  font-weight: 650;
  line-height: 1.3;
  color: var(--dashboard-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.character-card-source {
  margin-top: 4px;
  font-size: 13px;
  color: var(--dashboard-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.character-card-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.state-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 220px;
  border: 1px dashed var(--dashboard-border-strong);
  border-radius: 14px;
  color: var(--dashboard-muted);
  font-size: 14px;
}

.detail-section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--dashboard-text);
  letter-spacing: 0.01em;
}

.detail-section-count {
  font-weight: 400;
  color: var(--dashboard-muted);
  font-size: 13px;
}

.detail-empty-hint {
  color: var(--dashboard-muted);
  font-size: 13px;
  padding: 16px 0;
}

.image-gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  gap: 12px;
}

.image-gallery-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.image-gallery-thumb {
  width: 100%;
  aspect-ratio: 1;
  border-radius: 10px;
  border: 1px solid var(--dashboard-border);
  background: var(--dashboard-soft);
}

.image-gallery-filename {
  font-size: 11px;
  color: var(--dashboard-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.character-roleplay-page :deep(.v-switch .v-selection-control) {
  min-width: auto;
}

@media (max-width: 640px) {
  .character-grid {
    grid-template-columns: 1fr;
  }

  .character-card-inner {
    padding: 16px;
  }

  .image-gallery {
    grid-template-columns: repeat(auto-fill, minmax(80px, 1fr));
  }
}
</style>
