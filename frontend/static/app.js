const { createApp, ref, reactive, computed, watch, onMounted, nextTick } = Vue;
const PHASE_COLORS = {
  start: '#6aaa6a',
  plan: '#6a8aaa',
  step: '#aaaa6a',
  tool_call: '#9a8aaa',
  tool_result: '#8a90a0',
  explore: '#6a8aaa',
  edit: '#aaaa6a',
  verify: '#aa6a6a',
  replan: '#aa6a6a',
  verify_failed: '#aa6a6a',
  done: '#6aaa6a',
  subagent: '#6a8aaa',
  awaiting_input: '#aaaa6a',
  task_complete: '#6aaa6a',
  task_failed: '#aa6a6a',
  reflect: '#6a8aaa',
};

const CONTEXT_WINDOW_CHECKPOINTS = [
  { value: 4000, label: '4K' },
  { value: 8000, label: '8K' },
  { value: 16000, label: '16K' },
  { value: 32000, label: '32K' },
  { value: 64000, label: '64K' },
  { value: 128000, label: '128K' },
  { value: 200000, label: '200K' },
  { value: 1000000, label: '1M' },
];

createApp({
  setup() {
    const repoPath = ref('');
    const tier = ref('balanced');
    const mode = ref('chat');
    const swarm = ref(false);
    const connected = ref(false);
    const streaming = ref(false);
    const awaitingFeedback = ref(false);
    const apiRequesting = ref(false);
    const reasoningActive = ref(false);
    const reasoningStartTime = ref(0);
    const inputText = ref('');
    const planFeedbackText = ref('');
    const messages = ref([]);
    const pendingPlan = ref(null);
    const workingSet = ref(0);
    const modifiedCount = ref(0);
    const notesCount = ref(0);
    const agentStatus = ref('idle');
    const outputArea = ref(null);
    const eventSource = ref(null);
    const showSettings = ref(false);
    const settingsConfig = ref({});
    const settingsOriginal = ref({});
    const settingsStatus = ref('');
    const fetchedModels = ref({});
    const activityPanelOpen = ref(false);
    const activityLog = ref([]);
    const cancelToken = ref(null);
    const undoCounter = ref(0);
    const contextMenu = ref({ show: false, style: {}, msg: null, index: -1, canRegenerate: false });
    const lastSentMessage = ref('');

    // Diff tab state
    const viewTab = ref('chat');
    const diffs = ref([]);
    const diffFiles = ref([]);
    const diffLoading = ref(false);
    const diffError = ref('');

    const sessions = ref([]);
    const sessionsExpanded = ref(true);
    const selectedSession = ref(null);
    const selectedSessionDetail = ref(null);
    const sidebarTab = ref('folders');
    const recentFolders = ref([]);
    const recentFoldersExpanded = ref(true);
    const subagentAgents = ref([]);
    const subagentExpanded = ref(true);
    const selectedSubagent = ref(null);
    const selectedSubagentEntries = ref([]);
    const subagentProgressData = ref({});

    const contextWindowValue = ref(128000);
    const contextWindowEditing = ref(false);
    const contextWindowInput = ref('128000');

    function formatContextWindow(value) {
      if (value >= 1000000) return (value / 1000000).toFixed(1) + 'M';
      if (value >= 1000) return (value / 1000).toFixed(0) + 'K';
      return String(value);
    }

    function contextWindowSliderPos(value) {
      const minVal = 1000;
      const maxVal = 1000000;
      const pos = (Math.log(value) - Math.log(minVal)) / (Math.log(maxVal) - Math.log(minVal));
      return Math.max(0, Math.min(100, pos * 100));
    }

    function contextWindowFromSlider(pos) {
      const minVal = 1000;
      const maxVal = 1000000;
      const value = Math.round(Math.exp(
        (pos / 100) * (Math.log(maxVal) - Math.log(minVal)) + Math.log(minVal)
      ));
      for (const cp of CONTEXT_WINDOW_CHECKPOINTS) {
        const diff = Math.abs(value - cp.value);
        if (diff / cp.value < 0.1) return cp.value;
      }
      return value;
    }

    function onContextWindowSliderInput(e) {
      const pos = parseFloat(e.target.value);
      contextWindowValue.value = contextWindowFromSlider(pos);
    }

    function startContextWindowEdit() {
      contextWindowEditing.value = true;
      contextWindowInput.value = String(contextWindowValue.value);
    }

    function finishContextWindowEdit() {
      contextWindowEditing.value = false;
      const val = parseInt(contextWindowInput.value, 10);
      if (!isNaN(val) && val >= 1000 && val <= 10000000) {
        contextWindowValue.value = val;
      }
    }

    function selectContextCheckpoint(val) {
      contextWindowValue.value = val;
      contextWindowEditing.value = false;
    }

    function openSettings() {
      showSettings.value = true;
      settingsStatus.value = '';
      loadSettings();
    }

    function closeSettings() {
      showSettings.value = false;
      settingsConfig.value = JSON.parse(JSON.stringify(settingsOriginal.value));
    }

    async function loadSettings() {
      try {
        const r = await fetch('/api/settings');
        const d = await r.json();
        if (d.ok && d.config) {
          settingsConfig.value = d.config;
          settingsOriginal.value = JSON.parse(JSON.stringify(d.config));
          const profiles = d.config.profiles || {};
          const firstProfile = Object.values(profiles)[0];
          if (firstProfile && firstProfile.context_window) {
            contextWindowValue.value = firstProfile.context_window;
          }
        }
      } catch {}
    }

    async function saveSettings() {
      settingsStatus.value = 'Saving ...';
      if (settingsConfig.value.profiles) {
        for (const name in settingsConfig.value.profiles) {
          settingsConfig.value.profiles[name].context_window = contextWindowValue.value;
        }
      }
      try {
        const r = await fetch('/api/settings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(settingsConfig.value),
        });
        const d = await r.json();
        if (d.ok) {
          settingsStatus.value = 'Saved';
          settingsOriginal.value = JSON.parse(JSON.stringify(settingsConfig.value));
          setTimeout(() => { settingsStatus.value = ''; }, 2000);
        } else {
          settingsStatus.value = `Error: ${d.error}`;
        }
      } catch (e) {
        settingsStatus.value = `Error: ${e.message}`;
      }
    }

    function updateSetting(section, name, field, value) {
      if (!settingsConfig.value[section]) return;
      if (!settingsConfig.value[section][name]) return;
      settingsConfig.value[section][name][field] = value;
    }

    function updateRouter(key, value) {
      if (!settingsConfig.value.router) return;
      const num = Number(value);
      settingsConfig.value.router[key] = isNaN(num) ? value : num;
    }

    async function fetchProviderModels(base_url, api_key, profileName) {
      if (!base_url) return;
      try {
        const r = await fetch('/api/settings/fetch_models', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ base_url, api_key }),
        });
        const d = await r.json();
        if (d.ok && d.models) {
          fetchedModels.value[profileName] = d.models;
          fetchedModels.value = { ...fetchedModels.value };
        }
      } catch {}
    }

    // ===== Window Controls =====

    async function windowClose() {
      await fetch('/api/window/close', { method: 'POST' });
    }

    async function windowMinimize() {
      await fetch('/api/window/minimize', { method: 'POST' });
    }

    async function windowMaximize() {
      await fetch('/api/window/maximize', { method: 'POST' });
      // Reset drag tracking after maximize/restore
      dragPrev = { x: 0, y: 0 };
    }

    // ===== Window Dragging (titlebar only) =====
    const isDragging = ref(false);
    let dragPrev = { x: 0, y: 0 };
    let dragThrottle = false;

    function startDrag(e) {
      if (e.button !== 0) return;
      const target = e.target;
      if (target.closest('.titlebar-right, .titlebar-action-btn, .win-btn, .titlebar-window-controls')) return;
      isDragging.value = true;
      dragPrev = { x: e.screenX, y: e.screenY };
      document.addEventListener('mousemove', onDrag);
      document.addEventListener('mouseup', stopDrag);
      e.preventDefault();
    }

    function onDrag(e) {
      if (!isDragging.value || dragThrottle) return;
      const dx = e.screenX - dragPrev.x;
      const dy = e.screenY - dragPrev.y;
      if (Math.abs(dx) < 3 && Math.abs(dy) < 3) return;
      dragPrev = { x: e.screenX, y: e.screenY };
      dragThrottle = true;
      fetch('/api/window/move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dx, dy }),
      }).finally(() => { dragThrottle = false; });
    }

    function stopDrag() {
      isDragging.value = false;
      dragThrottle = false;
      document.removeEventListener('mousemove', onDrag);
      document.removeEventListener('mouseup', stopDrag);
    }

    function toggleActivityPanel() {
      activityPanelOpen.value = !activityPanelOpen.value;
    }

    function logActivity(text) {
      const now = new Date();
      const time = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`;
      activityLog.value.unshift({ time, text });
      if (activityLog.value.length > 50) activityLog.value.pop();
    }

    // ===== Sub-agent Progress =====
    const subagentPollInterval = ref(null);

    function startSubagentPoll() {
      stopSubagentPoll();
      subagentPollInterval.value = setInterval(fetchSubagentProgress, 2000);
    }

    function stopSubagentPoll() {
      if (subagentPollInterval.value) {
        clearInterval(subagentPollInterval.value);
        subagentPollInterval.value = null;
      }
    }

    async function fetchSubagentProgress() {
      try {
        const r = await fetch('/api/subagent_progress');
        const d = await r.json();
        if (d.ok && d.entries) {
          processSubagentEntries(d.entries);
        }
      } catch {}
    }

    function processSubagentEntries(entries) {
      const grouped = {};
      for (const entry of entries) {
        if (!grouped[entry.agent_id]) {
          grouped[entry.agent_id] = {
            agent_id: entry.agent_id,
            agent_type: entry.agent_type,
            entries: [],
            latest: entry,
          };
        }
        grouped[entry.agent_id].entries.push(entry);
        grouped[entry.agent_id].latest = entry;
      }
      subagentProgressData.value = grouped;
      const agentIds = new Set();
      for (const entry of entries) {
        agentIds.add(entry.agent_id);
      }
      subagentAgents.value = Array.from(agentIds).map(id => grouped[id] || { agent_id: id });
    }

    function viewSubagent(agentId) {
      selectedSubagent.value = agentId;
      const group = subagentProgressData.value[agentId];
      selectedSubagentEntries.value = group ? group.entries.slice(-50) : [];
    }

    function closeSubagentView() {
      selectedSubagent.value = null;
      selectedSubagentEntries.value = [];
    }

    function handleSubagentProgressEvent(data) {
      const entry = {
        agent_id: data.agent_id,
        agent_type: data.agent_type,
        status: data.status,
        phase: data.phase,
        detail: data.detail,
        step: data.step,
        total_steps: data.total_steps,
        turn: data.turn,
        progress_label: data.progress_label,
        files_modified: data.files_modified,
        files_read: data.files_read,
      };
      if (!subagentProgressData.value[entry.agent_id]) {
        subagentProgressData.value[entry.agent_id] = {
          agent_id: entry.agent_id,
          agent_type: entry.agent_type,
          entries: [],
          latest: entry,
        };
      }
      const group = subagentProgressData.value[entry.agent_id];
      group.entries.push(entry);
      if (group.entries.length > 100) group.entries.shift();
      group.latest = entry;
      const ids = Object.keys(subagentProgressData.value);
      subagentAgents.value = ids.map(id => subagentProgressData.value[id]);
    }

    const tiers = [
      { value: 'low', label: 'LOW' },
      { value: 'balanced', label: 'BALANCED' },
      { value: 'quality', label: 'QUALITY' },
    ];

    const statusClass = computed(() => {
      if (!connected.value) return 'offline';
      if (agentStatus.value === 'running') return 'running';
      if (agentStatus.value === 'awaiting_input') return 'awaiting';
      if (agentStatus.value === 'completed') return 'online';
      return 'idle';
    });

    const statusText = computed(() => {
      if (!connected.value) return 'OFFLINE';
      if (agentStatus.value === 'idle') return 'IDLE';
      if (agentStatus.value === 'running') return 'RUNNING';
      if (agentStatus.value === 'awaiting_input') return 'AWAITING';
      if (agentStatus.value === 'completed') return 'READY';
      if (agentStatus.value === 'failed') return 'FAILED';
      return agentStatus.value.toUpperCase();
    });

    const repoShort = computed(() => {
      if (!repoPath.value) return '.';
      const parts = repoPath.value.replace(/\\/g, '/').split('/');
      return parts[parts.length - 1] || parts[parts.length - 2] || '.';
    });

    const activeSubagents = computed(() => {
      return subagentAgents.value.filter(a => {
        const s = a.latest.status;
        return s === 'running' || s === 'idle';
      });
    });

    const subagentRunningCount = computed(() => {
      return subagentAgents.value.filter(a => a.latest.status === 'running').length;
    });

    function phaseColor(phase) {
      return PHASE_COLORS[phase] || '#8a90a0';
    }

    function formatPhase(phase) {
      return phase.toUpperCase().replace(/_/g, ' ');
    }

    function renderMarkdown(text) {
      if (!text) return '';
      const config = {
        breaks: true,
        gfm: true,
      };
      const html = marked.parse(text, config);
      return html.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '');
    }

    function formatArgs(args) {
      if (!args) return '';
      const str = JSON.stringify(args);
      return str.length > 80 ? str.slice(0, 80) + '...' : str;
    }

    const FILE_ICONS = {
      py:      '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#3572A5"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      js:      '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#f0db4f"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#323330" stroke-width="1.2" stroke-linecap="round"/></svg>',
      ts:      '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#3178c6"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      jsx:     '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#61dafb"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#20232a" stroke-width="1.2" stroke-linecap="round"/></svg>',
      tsx:     '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#3178c6"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      html:    '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#e34f26"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      css:     '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#563d7c"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      scss:    '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#c69"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      json:    '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#5a5a5a"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      yaml:    '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#6a4e2a"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      yml:     '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#6a4e2a"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      md:      '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#083fa1"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      vue:     '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#42b883"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      svelte:  '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#ff3e00"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      go:      '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#00add8"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      rs:      '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#dea584"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      java:    '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#b07219"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      cpp:     '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#f34b7d"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      c:       '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#555"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      h:       '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#555"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      sh:      '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#4eaa25"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      bash:    '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#4eaa25"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      ps1:     '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#012456"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      sql:     '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#e38c00"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      toml:    '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#8c8c8c"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      ini:     '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#8c8c8c"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      cfg:     '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#8c8c8c"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      dockerfile: '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#2496ed"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
      txt:     '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#555"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>',
    };

    function fileIcon(filename) {
      if (!filename) return FILE_ICONS.txt || '';
      const ext = filename.split('.').pop().toLowerCase();
      const name = filename.split('/').pop().toLowerCase();
      if (name === 'dockerfile') return FILE_ICONS.dockerfile;
      if (name === 'makefile') return '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#6d6d6d"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>';
      return FILE_ICONS[ext] || '<svg viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="12" height="14" rx="2" fill="#555"/><path d="M5 4h6M5 7h6M5 10h4" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/></svg>';
    }

    async function loadDiffs() {
      diffLoading.value = true;
      diffError.value = '';
      try {
        const r = await fetch('/api/diffs');
        const d = await r.json();
        if (d.ok) {
          diffs.value = d.diffs || [];
          diffFiles.value = d.files || [];
        } else {
          diffError.value = d.error || 'Failed to load diffs';
        }
      } catch (e) {
        diffError.value = e.message;
      }
      diffLoading.value = false;
    }

    function scrollToBottom() {
      nextTick(() => {
        if (outputArea.value) {
          const el = outputArea.value;
          el.scrollTop = el.scrollHeight;
        }
      });
    }

    watch(messages, scrollToBottom, { deep: true });

    let statusInterval = null;

    function startStatusPoll() {
      stopStatusPoll();
      statusInterval = setInterval(fetchStatus, 2000);
    }

    function stopStatusPoll() {
      if (statusInterval) {
        clearInterval(statusInterval);
        statusInterval = null;
      }
    }

    async function fetchStatus() {
      try {
        const r = await fetch('/api/status');
        const data = await r.json();
        if (data.connected) {
          connected.value = true;
          repoPath.value = data.repo || '';
          agentStatus.value = data.status || 'idle';
          workingSet.value = data.working_set || 0;
          modifiedCount.value = data.modified_files?.length || 0;
          notesCount.value = data.session_notes || 0;
          if (data.status === 'awaiting_input' && !awaitingFeedback.value && !streaming.value) {
            const pr = await fetch('/api/pending_plan');
            const planData = await pr.json();
            if (planData.ok) {
              pendingPlan.value = planData.plan;
            }
          }
          if (data.status !== 'awaiting_input' && pendingPlan.value) {
            pendingPlan.value = null;
          }
        } else {
          connected.value = false;
          repoPath.value = '';
          agentStatus.value = 'idle';
          workingSet.value = 0;
          modifiedCount.value = 0;
          notesCount.value = 0;
        }
      } catch {
        connected.value = false;
      }
    }

    function abortStream() {
      if (eventSource.value) {
        eventSource.value.close();
        eventSource.value = null;
      }
      streaming.value = false;
    }

    async function cancelAction() {
      if (!streaming.value) return;
      try {
        await fetch('/api/cancel', { method: 'POST' });
        logActivity('Cancelled ongoing action');
      } catch (e) {
        logActivity(`Cancel failed: ${e.message}`);
      }
      abortStream();
      messages.value.push({ type: 'error', text: 'Action cancelled by user.' });
      agentStatus.value = 'idle';
      streaming.value = false;
    }

    async function undoEdit(undoId) {
      try {
        const r = await fetch('/api/undo', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ undo_id: undoId }),
        });
        const data = await r.json();
        if (data.ok) {
          const msg = messages.value.find(m => m.undoId === undoId);
          if (msg) {
            msg.canUndo = false;
          }
          messages.value.push({ type: 'text', text: `Undone. Restored to state before edit.${data.reverted_files ? ` Files: ${data.reverted_files.join(', ')}` : ''}` });
          logActivity(`Undid edit #${undoId}`);
          await fetchStatus();
        } else {
          messages.value.push({ type: 'error', text: `Undo failed: ${data.error || 'unknown error'}` });
        }
      } catch (err) {
        messages.value.push({ type: 'error', text: `Undo error: ${err.message}` });
      }
    }

    async function sendMessage(skipUserMsg = false) {
      const text = inputText.value.trim();
      if (!text || !connected.value || streaming.value) return;
      if (text.startsWith('/help')) {
        messages.value.push({ type: 'text', text: [
          '/task <desc> - Run a full agent task',
          '/approve - Approve plan',
          '/reset - Clear context',
          '/status - Show context info',
          '/exit - Disconnect',
        ].join('\n') });
        inputText.value = '';
        return;
      }
      if (text.startsWith('/status')) {
        await fetchStatus();
        inputText.value = '';
        return;
      }
      if (text.startsWith('/reset') || text.startsWith('/clear')) {
        await resetAgent();
        inputText.value = '';
        return;
      }
      if (text.startsWith('/exit')) {
        await clearAgent();
        inputText.value = '';
        return;
      }
      if (text.startsWith('/approve')) {
        await sendFeedback('approved');
        inputText.value = '';
        return;
      }
      let sendMode = mode.value;
      let sendText = text;
      if (text.startsWith('/task ')) {
        sendMode = 'task';
        sendText = text.slice(6);
      }
      if (!skipUserMsg) {
        messages.value.push({ type: 'user', text: sendText });
      }
      pendingPlan.value = null;
      if (!skipUserMsg) {
        lastSentMessage.value = sendText;
      }
      inputText.value = '';
      streaming.value = true;
      logActivity(`Started ${sendMode}: ${sendText.slice(0, 40)}${sendText.length > 40 ? '...' : ''}`);
      try {
        const resp = await fetch('/api/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: sendText, mode: sendMode }),
        });
        if (!resp.ok) {
          const err = await resp.json();
          messages.value.push({ type: 'error', text: err.error || 'Request failed' });
          streaming.value = false;
          return;
        }
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6));
                handleStreamData(data);
              } catch {}
            }
          }
        }
        if (buffer.startsWith('data: ')) {
          try {
            const data = JSON.parse(buffer.slice(6));
            handleStreamData(data);
          } catch {}
        }
      } catch (err) {
        messages.value.push({ type: 'error', text: `Connection error: ${err.message}` });
      }
      streaming.value = false;
      stopSubagentPoll();
      await fetchStatus();
      await fetchSubagentProgress();
      await loadSessions();
    }

    function handleStreamData(data) {
      if (!data || !data.type) return;
      switch (data.type) {
        case 'subagent_progress':
          handleSubagentProgressEvent(data);
          break;
        case 'trace':
          if (data.phase === 'awaiting_input' && data.payload?.plan) {
            pendingPlan.value = data.payload.plan;
          }
          messages.value.push({
            type: 'trace',
            phase: data.phase,
            detail: data.detail,
            payload: data.payload,
          });
          break;
        case 'chunk':
          // When we receive actual content (text, tool_calls, tool_result, or done),
          // clear any stale progress messages first so they don't stick around.
          if (data.text || data.tool_calls || data.tool_result || data.error) {
            const last = messages.value[messages.value.length - 1];
            if (last && last.type === 'progress') {
              messages.value.pop();
            }
          }
          if (data.text) {
            const last = messages.value[messages.value.length - 1];
            if (last && last.type === 'text' && !last._finalized) {
              last.text += data.text;
            } else {
              messages.value.push({ type: 'text', text: data.text, _finalized: false });
            }
          }
          if (data.reasoning && !reasoningActive.value) {
            reasoningActive.value = true;
            reasoningStartTime.value = Date.now();
            messages.value.push({ type: 'reasoning' });
          } else if (!data.reasoning && reasoningActive.value) {
            reasoningActive.value = false;
            const elapsed = Date.now() - reasoningStartTime.value;
            const label = elapsed < 60000
              ? `Thought for ${Math.round(elapsed / 1000)}s`
              : `Thought for ${Math.floor(elapsed / 60000)}m ${Math.round((elapsed % 60000) / 1000)}s`;
            // Replace the reasoning message with a done thought label
            const last = messages.value[messages.value.length - 1];
            if (last && last.type === 'reasoning') {
              messages.value[messages.value.length - 1] = { type: 'thought', text: label };
            } else {
              messages.value.push({ type: 'thought', text: label });
            }
          }
          if (data.tool_calls) {
            messages.value.push({ type: 'tool_calls', toolCalls: data.tool_calls });
          }
          if (data.tool_result) {
            const isDiff = data.tool_result.includes('--- a/') || data.tool_result.includes('+++ b/');
            const lines = data.tool_result.split('\n');
            const previewLines = lines.slice(0, 5);
            messages.value.push({
              type: 'tool_result',
              text: data.tool_result,
              _preview: previewLines.join('\n'),
              _lineCount: lines.length,
              _collapsed: lines.length > 8,
              isDiff,
            });
          }
          if (data.error) {
            messages.value.push({ type: 'error', text: data.error });
          }
          if (data.progress_label !== undefined && data.progress_label !== null) {
            if (data.progress_label === "") {
              // Empty progress label: clear any existing progress message
              const last = messages.value[messages.value.length - 1];
              if (last && last.type === 'progress') {
                messages.value.pop();
              }
              apiRequesting.value = false;
            } else {
              // API request is active when we see progress labels from the LLM
              apiRequesting.value = true;
              const last = messages.value[messages.value.length - 1];
              if (last && last.type === 'progress') {
                last.text = data.progress_label;
              } else {
                messages.value.push({ type: 'progress', text: data.progress_label });
              }
            }
          }
          if (data.done) {
            apiRequesting.value = false;
            // Always clear any stale progress message when we're done
            const last = messages.value[messages.value.length - 1];
            if (last && last.type === 'progress') {
              messages.value.pop();
            }
            // Finalize the last text message if there is one
            const newLast = messages.value[messages.value.length - 1];
            if (newLast && newLast.type === 'text') {
              newLast._finalized = true;
            }
          }
          if (data.status) {
            agentStatus.value = data.status;
          }
          break;
        case 'edit_snapshot':
          undoCounter.value += 1;
          const undoId = undoCounter.value;
          messages.value.push({
            type: 'text',
            text: 'Edit completed.',
            canUndo: true,
            undoId: undoId,
            _finalized: true,
          });
          logActivity(`Edit made (undo #${undoId} available)`);
          break;
        case 'done':
          agentStatus.value = data.status || 'completed';
          if (data.status === 'completed') {
            messages.value.push({ type: 'done', status: 'completed' });
          } else if (data.status === 'failed') {
            messages.value.push({ type: 'done', status: 'failed' });
          }
          break;
        case 'keepalive':
          break;
      }
    }

    async function sendFeedback(feedback) {
      if (!feedback || feedback === 'rejected') {
        feedback = 'rejected';
      }
      awaitingFeedback.value = true;
      try {
        const r = await fetch('/api/feedback', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ feedback }),
        });
        const data = await r.json();
        if (data.ok) {
          pendingPlan.value = null;
          planFeedbackText.value = '';
        } else {
          messages.value.push({ type: 'error', text: data.error || 'Feedback failed' });
        }
      } catch (err) {
        messages.value.push({ type: 'error', text: `Feedback error: ${err.message}` });
      }

      // If approved, the backend handles execution within the same SSE stream.
      // No re-send needed — the stream reader will receive the execution results.
      // Just clear the plan bar; the existing stream will deliver everything.
      if (feedback === 'approved') {
        pendingPlan.value = null;
      }

      awaitingFeedback.value = false;
    }

    async function pickFolder() {
      try {
        const r = await fetch('/api/pick_folder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        });
        const data = await r.json();
        if (data.ok && data.path) {
          await initAgent(data.path);
        }
      } catch {
        const path = prompt('Enter workspace folder path:');
        if (path) {
          await initAgent(path);
        }
      }
    }

    async function initAgent(path) {
      try {
        const r = await fetch('/api/init', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            repo_path: path,
            tier: tier.value,
            swarm: swarm.value,
          }),
        });
        const data = await r.json();
        if (data.ok) {
          messages.value.push({ type: 'text', text: `Initialized agent at ${data.repo}` });
          messages.value.push({ type: 'text', text: `Tier: ${data.tier.toUpperCase()} | Swarm: ${data.swarm ? 'ON' : 'OFF'}` });
          logActivity(`Agent initialized at ${data.repo}`);
          await fetchStatus();
          await loadRecentFolders();
          await loadSessions();
        } else {
          messages.value.push({ type: 'error', text: data.error || 'Init failed' });
        }
      } catch (err) {
        messages.value.push({ type: 'error', text: `Init error: ${err.message}` });
      }
    }

    async function resetAgent() {
      try {
        await fetch('/api/reset', { method: 'POST' });
        messages.value = [];
        logActivity('Session reset');
      } catch {}
    }

    async function clearAgent() {
      try {
        await fetch('/api/clear_agent', { method: 'POST' });
        messages.value.push({ type: 'text', text: 'Agent cleared.' });
        connected.value = false;
        repoPath.value = '';
        agentStatus.value = 'idle';
        workingSet.value = 0;
        modifiedCount.value = 0;
        notesCount.value = 0;
        logActivity('Agent cleared');
      } catch {}
    }

    // ===== Session Functions =====
    async function loadSessions() {
      if (!connected.value || !repoPath.value) return;
      try {
        const r = await fetch(`/api/sessions?repo=${encodeURIComponent(repoPath.value)}`);
        const data = await r.json();
        if (data.ok) {
          sessions.value = data.sessions || [];
        }
      } catch {}
    }

    const viewingSessionMessages = ref(null);
    const viewingSessionLabel = ref('');

    async function viewSession(sessionId) {
      if (!connected.value || !repoPath.value) return;
      selectedSession.value = sessionId;
      selectedSessionDetail.value = null;
      try {
        const r = await fetch(`/api/sessions/${sessionId}?repo=${encodeURIComponent(repoPath.value)}`);
        const data = await r.json();
        if (data.ok) {
          selectedSessionDetail.value = data;
        }
      } catch {}
    }

    async function loadSessionChat(sessionId) {
      if (!repoPath.value) return;
      try {
        const r = await fetch(`/api/sessions/${sessionId}/messages?repo=${encodeURIComponent(repoPath.value)}`);
        const data = await r.json();
        if (data.ok && data.messages && data.messages.length > 0) {
          const sess = sessions.value.find(s => s.id === sessionId);
          viewingSessionLabel.value = sess ? (sess.task || 'Previous Session').slice(0, 50) : 'Previous Session';
          viewingSessionMessages.value = sessionId;
          messages.value = data.messages.map(m => ({
            type: m.type,
            text: m.text,
            _finalized: true,
            _from_history: true,
          }));
          messages.value.unshift({
            type: 'text',
            text: `Viewing session: ${viewingSessionLabel.value} -- these are read-only historical messages.`,
            _finalized: true,
            _from_history: true,
            _session_banner: true,
          });
        } else {
          messages.value.push({ type: 'error', text: 'No messages found for this session.' });
        }
      } catch (err) {
        messages.value.push({ type: 'error', text: `Failed to load session chat: ${err.message}` });
      }
    }

    function closeSessionChat() {
      viewingSessionMessages.value = null;
      viewingSessionLabel.value = '';
      messages.value = [];
    }

    function closeSessionDetail() {
      selectedSession.value = null;
      selectedSessionDetail.value = null;
    }

    function formatSessionDate(dateStr) {
      if (!dateStr) return 'N/A';
      try {
        const d = new Date(dateStr);
        const pad = n => n.toString().padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
      } catch {
        return dateStr;
      }
    }

    function sessionStatusLabel(status) {
      if (!status) return 'N/A';
      return status.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    // ===== Recent Folders Functions =====
    async function loadRecentFolders() {
      try {
        const r = await fetch('/api/recent_folders');
        const data = await r.json();
        if (data.ok) {
          recentFolders.value = data.folders || [];
        }
      } catch {}
    }

    async function clearRecentFolders() {
      try {
        await fetch('/api/recent_folders/clear', { method: 'POST' });
        recentFolders.value = [];
      } catch {}
    }

    async function openRecentFolder(path) {
      await initAgent(path);
    }

    function toggleToolResult(msg) {
      msg._collapsed = !msg._collapsed;
    }

    // ===== Context Menu =====
    function showContextMenu(e, msg, index) {
      const x = e.clientX;
      const y = e.clientY;
      const menuWidth = 200;
      const menuHeight = 160;
      const maxX = window.innerWidth - menuWidth;
      const maxY = window.innerHeight - menuHeight;
      contextMenu.value = {
        show: true,
        style: {
          left: Math.min(x, maxX) + 'px',
          top: Math.min(y, maxY) + 'px',
        },
        msg,
        index,
        canRegenerate: index === messages.value.length - 1 && msg.type === 'user' && msg.text === lastSentMessage.value,
      };
    }

    function closeContextMenu() {
      contextMenu.value.show = false;
    }

    function copyMessage(msg) {
      if (!msg) return;
      const text = msg.type === 'tool_result' ? msg.text : msg.text || '';
      navigator.clipboard.writeText(text).catch(() => {});
      closeContextMenu();
    }

    function copyMessageMarkdown(msg) {
      if (!msg) return;
      const text = msg.type === 'tool_result'
        ? '```\n' + msg.text + '\n```'
        : msg.text || '';
      navigator.clipboard.writeText(text).catch(() => {});
      closeContextMenu();
    }

    function regenerateMessage(msg) {
      if (!msg || msg.type !== 'user') return;
      inputText.value = msg.text;
      closeContextMenu();
    }

    function regenerateLastUserMessage() {
      const userMsg = messages.value.findLast(m => m.type === 'user');
      if (userMsg && userMsg.text) {
        inputText.value = userMsg.text;
      }
      closeContextMenu();
    }

    function deleteMessage(index) {
      if (index >= 0 && index < messages.value.length) {
        messages.value.splice(index, 1);
      }
      closeContextMenu();
    }

    // ===== On Mount =====
    onMounted(() => {
      startStatusPoll();
      loadRecentFolders();
      fetchStatus();
    });

    return {
      repoPath, tier, mode, swarm, connected, streaming, awaitingFeedback, apiRequesting,
      reasoningActive, reasoningStartTime, inputText, planFeedbackText, messages, pendingPlan,
      workingSet, modifiedCount, notesCount, agentStatus, outputArea, eventSource,
      showSettings, settingsConfig, settingsOriginal, settingsStatus, fetchedModels,
      activityPanelOpen, activityLog, cancelToken, undoCounter, contextMenu, lastSentMessage,
      viewTab, diffs, diffFiles, diffLoading, diffError,
      sessions, sessionsExpanded, selectedSession, selectedSessionDetail, sidebarTab,
      recentFolders, recentFoldersExpanded, subagentAgents, subagentExpanded,
      selectedSubagent, selectedSubagentEntries, subagentProgressData,
      contextWindowValue, contextWindowEditing, contextWindowInput,
      CONTEXT_WINDOW_CHECKPOINTS,
      formatContextWindow, contextWindowSliderPos, contextWindowFromSlider,
      onContextWindowSliderInput, startContextWindowEdit, finishContextWindowEdit,
      selectContextCheckpoint,
      tiers, statusClass, statusText, repoShort, activeSubagents, subagentRunningCount,
      phaseColor, formatPhase, renderMarkdown, formatArgs, fileIcon,
      loadDiffs, abortStream, cancelAction, undoEdit, sendMessage,
      sendFeedback, pickFolder, initAgent, resetAgent, clearAgent,
      loadSessions, viewSession, loadSessionChat, closeSessionChat, closeSessionDetail,
      formatSessionDate, sessionStatusLabel,
      loadRecentFolders, clearRecentFolders, openRecentFolder,
      toggleToolResult, showContextMenu, closeContextMenu, copyMessage,
      copyMessageMarkdown, regenerateMessage, regenerateLastUserMessage, deleteMessage,
      windowClose, windowMinimize, windowMaximize, startDrag,
      toggleActivityPanel,
      viewingSessionMessages, viewingSessionLabel,
      fetchStatus, stopStatusPoll, startSubagentPoll, stopSubagentPoll,
    };
  }
}).mount('#app');