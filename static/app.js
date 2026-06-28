document.addEventListener('DOMContentLoaded', () => {
  // DOM Elements
  const statusDot = document.getElementById('status-dot');
  const statusText = document.getElementById('status-text');
  const statusPills = document.getElementById('status-pills');
  const goalForm = document.getElementById('goal-form');
  const goalInput = document.getElementById('goal-input');
  const campaignNameInput = document.getElementById('campaign-name');
  const channelHintSelect = document.getElementById('channel-hint');
  const idempotencyKeyInput = document.getElementById('idempotency-key');
  const regenKeyBtn = document.getElementById('regen-key-btn');
  const submitBtn = document.getElementById('submit-btn');
  const submitBtnText = submitBtn.querySelector('.btn-text');
  const submitBtnSpinner = submitBtn.querySelector('.btn-spinner');

  // Trace elements
  const traceCard = document.getElementById('trace-card');
  const traceStatusBadge = document.getElementById('trace-status-badge');
  const tracePlaceholder = document.getElementById('trace-placeholder');
  const traceLogContainer = document.getElementById('trace-log-container');
  const metaTraceId = document.getElementById('meta-trace-id');
  const metaDuration = document.getElementById('meta-duration');
  const metaTokens = document.getElementById('meta-tokens');
  const metaCost = document.getElementById('meta-cost');
  const stepsTimeline = document.getElementById('steps-timeline');
  const outcomeBox = document.getElementById('outcome-box');
  const outcomeTitle = document.getElementById('outcome-title');
  const outcomeMessage = document.getElementById('outcome-message');

  // Campaign Preview elements
  const outcomeCampaign = document.getElementById('outcome-campaign');
  const previewCampaignName = document.getElementById('preview-campaign-name');
  const previewCampaignChannel = document.getElementById(
    'preview-campaign-channel',
  );
  const previewReach = document.getElementById('preview-reach');
  const previewSubject = document.getElementById('preview-subject');
  const previewBody = document.getElementById('preview-body');
  const previewLinkField = document.getElementById('preview-link-field');
  const previewLink = document.getElementById('preview-link');

  // Campaigns Library elements
  const campaignsTbody = document.getElementById('campaigns-tbody');
  const refreshCampaignsBtn = document.getElementById('refresh-campaigns-btn');
  const runsTbody = document.getElementById('runs-tbody');
  const refreshRunsBtn = document.getElementById('refresh-runs-btn');

  // Tab elements
  const tabCampaigns = document.getElementById('tab-campaigns');
  const tabRuns = document.getElementById('tab-runs');
  const campaignsTableContainer = document.getElementById(
    'campaigns-table-container',
  );
  const runsTableContainer = document.getElementById('runs-table-container');

  // Modal elements
  const campaignModal = document.getElementById('campaign-modal');
  const modalOverlay = document.getElementById('modal-overlay');
  const closeModalBtn = document.getElementById('close-modal-btn');
  const modalBodyContent = document.getElementById('modal-body-content');

  // Initialize Page
  generateIdempotencyKey();
  checkSystemHealth();
  loadCampaigns();
  loadRuns();

  // Helper: Generate UUID for Idempotency Key
  function generateIdempotencyKey() {
    const uuid =
      'key_' +
      Math.random().toString(36).substring(2, 15) +
      Math.random().toString(36).substring(2, 15);
    idempotencyKeyInput.value = uuid;
  }

  regenKeyBtn.addEventListener('click', generateIdempotencyKey);

  // Suggestions pre-filling
  document.querySelectorAll('.suggest-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      goalInput.value = btn.dataset.goal;
      campaignNameInput.value = btn.dataset.name;
      channelHintSelect.value = btn.dataset.channel;
      // Scroll to form
      goalInput.focus();
    });
  });

  // Check system status
  async function checkSystemHealth() {
    try {
      const res = await fetch('/health');
      const data = await res.json();

      if (data.status === 'ok') {
        statusDot.className = 'status-dot ok';
        statusText.textContent = `Online (As of: ${data.as_of_date || 'N/A'})`;
      } else {
        statusDot.className = 'status-dot warning';
        statusText.textContent = 'System degraded';
      }

      // Create status badges/pills
      statusPills.innerHTML = '';

      // LLM Configured Pill
      createPill('LLM Configured', data.llm_configured);
      // Embeddings Loaded Pill
      createPill('RAG Embeddings', data.embeddings_loaded);
      // Model Chain Pill
      if (data.model_chain) {
        const modelPill = document.createElement('span');
        modelPill.className = 'pill';
        modelPill.style.color = '#a855f7';
        modelPill.textContent = `Model: ${data.model_chain}`;
        statusPills.appendChild(modelPill);
      }
      // User Metrics Rows Pill
      if (data.user_metrics_rows !== null) {
        const rowsPill = document.createElement('span');
        rowsPill.className = 'pill';
        rowsPill.textContent = `${data.user_metrics_rows.toLocaleString()} users indexed`;
        statusPills.appendChild(rowsPill);
      }
    } catch (error) {
      statusDot.className = 'status-dot error';
      statusText.textContent = 'Server disconnected';
      statusPills.innerHTML =
        '<span class="pill not-configured">Offline</span>';
    }
  }

  function createPill(label, isConfigured) {
    const pill = document.createElement('span');
    pill.className = `pill ${isConfigured ? 'configured' : 'not-configured'}`;
    pill.textContent = `${label}: ${isConfigured ? 'Active' : 'Disabled'}`;
    statusPills.appendChild(pill);
  }

  // Submit Marketing Goal to Copilot Agent
  goalForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const goal = goalInput.value.trim();
    const name = campaignNameInput.value.trim() || null;
    const channel_hint = channelHintSelect.value || null;
    const idempotencyKey = idempotencyKeyInput.value.trim();

    if (!goal) return;

    // UI state: loading
    submitBtn.disabled = true;
    submitBtnText.textContent = 'Agent executing...';
    submitBtnSpinner.classList.remove('hidden');

    tracePlaceholder.classList.add('hidden');
    traceLogContainer.classList.remove('hidden');
    traceStatusBadge.classList.remove('hidden');
    traceStatusBadge.className = 'badge running';
    traceStatusBadge.textContent = 'running';

    // Reset trace details
    metaTraceId.textContent = 'Pending...';
    metaDuration.textContent = 'Running...';
    metaTokens.textContent = 'Running...';
    metaCost.textContent = 'Running...';
    stepsTimeline.innerHTML = `
            <div class="step-card model">
                <div class="step-header">
                    <span class="step-title">Initializing Agentic Session</span>
                    <span class="step-meta">Just now</span>
                </div>
                <div class="step-summary">Analyzing marketing goal and loading customer segmentation compiler...</div>
            </div>
        `;
    outcomeBox.classList.add('hidden');
    outcomeCampaign.classList.add('hidden');

    try {
      const res = await fetch('/copilot/run', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({ goal, name, channel_hint }),
      });

      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(
          errorData.detail || 'An error occurred during execution',
        );
      }

      const runResult = await res.json();
      const traceId = runResult.trace_id;
      metaTraceId.textContent = traceId;

      // Start polling
      let pollAttempts = 0;
      const maxPollAttempts = 120; // 3 minutes max
      const pollInterval = 1500; // 1.5 seconds

      while (pollAttempts < maxPollAttempts) {
        const traceRes = await fetch(`/runs/${traceId}`);
        if (!traceRes.ok) {
          throw new Error(`Failed to fetch trace status for ${traceId}`);
        }
        const trace = await traceRes.json();

        // Update meta details
        metaDuration.textContent = trace.total_ms
          ? `${Math.round(trace.total_ms).toLocaleString()} ms`
          : 'Running...';
        metaTokens.textContent = (trace.total_tokens || 0).toLocaleString();
        metaCost.textContent = `$${(trace.est_cost || 0).toFixed(4)}`;

        // Update status badge
        if (trace.status === 'in_progress') {
          traceStatusBadge.className = 'badge running';
          traceStatusBadge.textContent = 'running';
        } else if (trace.status === 'created') {
          traceStatusBadge.className = 'badge completed';
          traceStatusBadge.textContent = 'completed';
        } else {
          traceStatusBadge.className = 'badge failed';
          traceStatusBadge.textContent = trace.status || 'failed';
        }

        // Render latest steps
        renderSteps(trace.steps);

        // If not in progress, handle outcome and break
        if (trace.status !== 'in_progress') {
          outcomeBox.classList.remove('hidden');
          outcomeBox.className = `outcome-box ${trace.status === 'created' ? 'success' : 'failed'}`;

          if (trace.status === 'created') {
            outcomeTitle.textContent = 'Campaign Created Successfully!';
            outcomeMessage.textContent =
              trace.message ||
              'The campaign was successfully compiled, validated, and saved to the database.';

            if (trace.campaign) {
              updateCampaignPreview(trace.campaign);
            }
          } else {
            outcomeTitle.textContent = 'Unsupported Goal or Failed Policy';
            outcomeMessage.textContent =
              trace.message ||
              'The request could not be fulfilled by the agent guidelines.';
          }
          break;
        }

        pollAttempts++;
        await new Promise((resolve) => setTimeout(resolve, pollInterval));
      }

      if (pollAttempts >= maxPollAttempts) {
        throw new Error(
          'Agent execution timed out. Please check back later or refresh.',
        );
      }

      // Refresh campaigns library
      loadCampaigns();
      loadRuns();
    } catch (err) {
      traceStatusBadge.className = 'badge failed';
      traceStatusBadge.textContent = 'error';
      outcomeBox.classList.remove('hidden');
      outcomeBox.className = 'outcome-box failed';
      outcomeTitle.textContent = 'Execution Failed';
      outcomeMessage.textContent = err.message;

      // Add error trace card
      const errCard = document.createElement('div');
      errCard.className = 'step-card error';
      errCard.innerHTML = `
                <div class="step-header">
                    <span class="step-title">Execution Interrupted</span>
                    <span class="step-meta">Just now</span>
                </div>
                <div class="step-summary">${err.message}</div>
            `;
      stepsTimeline.appendChild(errCard);
    } finally {
      // Restore UI trigger state
      submitBtn.disabled = false;
      submitBtnText.textContent = 'Run Campaign Copilot';
      submitBtnSpinner.classList.add('hidden');
    }
  });

  // Fetch details of trace steps from runs DB
  async function fetchAndRenderTraceSteps(traceId) {
    try {
      const res = await fetch(`/runs/${traceId}`);
      if (!res.ok) return;
      const data = await res.json();

      renderSteps(data.steps);
    } catch (error) {
      console.error('Error loading trace steps:', error);
    }
  }

  // Load Campaign Library
  async function loadCampaigns() {
    try {
      const res = await fetch('/campaigns');
      if (!res.ok) throw new Error('Could not fetch campaigns');
      const campaigns = await res.json();

      if (campaigns.length === 0) {
        campaignsTbody.innerHTML = `
                    <tr>
                        <td colspan="7" class="td-placeholder">No campaigns generated yet. Run the Copilot to create one.</td>
                    </tr>
                `;
        return;
      }

      campaignsTbody.innerHTML = '';
      // Display campaigns, newer first
      campaigns.reverse().forEach((campaign) => {
        const tr = document.createElement('tr');
        tr.addEventListener('click', () => showCampaignDetail(campaign));

        const timeText = campaign.created_at
          ? new Date(campaign.created_at).toLocaleString()
          : 'N/A';
        const msg = campaign.message || {};
        const subjectText = msg.subject || msg.title || campaign.subject || campaign.title || '';
        const bodyText = msg.body || campaign.body || '';
        const fullMessageText = subjectText 
          ? (bodyText ? `[${subjectText}] ${bodyText}` : subjectText) 
          : (bodyText || '-');

        tr.innerHTML = `
                    <td class="cell-mono">${escapeHtml(campaign.campaign_id)}</td>
                    <td class="font-medium">${escapeHtml(campaign.name || 'Unnamed')}</td>
                    <td><span class="campaign-channel-badge" style="background-color: ${getChannelColor(campaign.channel)}">${escapeHtml(campaign.channel || 'push')}</span></td>
                    <td class="font-medium">${(campaign.segment_size || 0).toLocaleString()}</td>
                    <td>${timeText}</td>
                `;

        campaignsTbody.appendChild(tr);
      });
    } catch (error) {
      campaignsTbody.innerHTML = `
                <tr>
                    <td colspan="5" class="td-placeholder text-error">Failed to load campaigns: ${error.message}</td>
                </tr>
            `;
    }
  }

  refreshCampaignsBtn.addEventListener('click', loadCampaigns);

  // Load Observability Runs Library
  async function loadRuns() {
    try {
      const res = await fetch('/runs');
      if (!res.ok) throw new Error('Could not fetch runs');
      const runs = await res.json();

      if (runs.length === 0) {
        runsTbody.innerHTML = `
                    <tr>
                        <td colspan="7" class="td-placeholder">No execution traces found. Run the Copilot to create one.</td>
                    </tr>
                `;
        return;
      }

      runsTbody.innerHTML = '';
      runs.forEach((run) => {
        const tr = document.createElement('tr');
        tr.addEventListener('click', () => showHistoricalTrace(run));

        const timeText = run.created_at
          ? new Date(run.created_at).toLocaleString()
          : 'N/A';
        const durationText = run.total_ms
          ? `${Math.round(run.total_ms).toLocaleString()} ms`
          : '-';

        let statusBadge = '';
        if (run.status === 'in_progress') {
          statusBadge = '<span class="badge running">running</span>';
        } else if (run.status === 'created') {
          statusBadge = '<span class="badge completed">completed</span>';
        } else {
          statusBadge = `<span class="badge failed">${escapeHtml(run.status)}</span>`;
        }

        tr.innerHTML = `
                    <td class="cell-mono">${escapeHtml(run.trace_id)}</td>
                    <td class="font-medium"><span class="truncate-cell" style="max-width: 250px;" title="${escapeHtml(run.goal)}">${escapeHtml(run.goal)}</span></td>
                    <td>${statusBadge}</td>
                    <td>${durationText}</td>
                    <td>${(run.total_tokens || 0).toLocaleString()}</td>
                    <td class="font-mono text-accent">$${(run.est_cost || 0).toFixed(4)}</td>
                    <td>${timeText}</td>
                `;

        runsTbody.appendChild(tr);
      });
    } catch (error) {
      runsTbody.innerHTML = `
                <tr>
                    <td colspan="7" class="td-placeholder text-error">Failed to load runs: ${error.message}</td>
                </tr>
            `;
    }
  }

  refreshRunsBtn.addEventListener('click', loadRuns);

  // Display chosen historical trace details inside trace panel
  function showHistoricalTrace(trace) {
    tracePlaceholder.classList.add('hidden');
    traceLogContainer.classList.remove('hidden');
    traceStatusBadge.classList.remove('hidden');

    // Update Metadata
    metaTraceId.textContent = trace.trace_id || '-';
    metaDuration.textContent = trace.total_ms
      ? `${Math.round(trace.total_ms).toLocaleString()} ms`
      : 'N/A';
    metaTokens.textContent = (trace.total_tokens || 0).toLocaleString();
    metaCost.textContent = `$${(trace.est_cost || 0).toFixed(4)}`;

    // Update status badge
    if (trace.status === 'in_progress') {
      traceStatusBadge.className = 'badge running';
      traceStatusBadge.textContent = 'running';
    } else if (trace.status === 'created') {
      traceStatusBadge.className = 'badge completed';
      traceStatusBadge.textContent = 'completed';
    } else {
      traceStatusBadge.className = 'badge failed';
      traceStatusBadge.textContent = trace.status || 'failed';
    }

    renderSteps(trace.steps);

    // Show outcome details
    outcomeBox.classList.remove('hidden');
    outcomeBox.className = `outcome-box ${trace.status === 'created' ? 'success' : 'failed'}`;

    if (trace.status === 'created') {
      outcomeTitle.textContent = 'Campaign Created Successfully!';
      outcomeMessage.textContent =
        trace.message ||
        'The campaign was successfully compiled, validated, and saved to the database.';

      if (trace.campaign) {
        updateCampaignPreview(trace.campaign);
      } else if (trace.campaign_id) {
        // Fetch the campaign dynamically and render
        fetch(`/campaigns/${trace.campaign_id}`)
          .then((res) => (res.ok ? res.json() : null))
          .then((campaign) => {
            updateCampaignPreview(campaign);
          })
          .catch(() => {
            updateCampaignPreview(null);
          });
      } else {
        updateCampaignPreview(null);
      }
    } else {
      outcomeCampaign.classList.add('hidden');

      outcomeTitle.textContent = 'Unsupported Goal or Failed Policy';
      outcomeMessage.textContent =
        trace.message ||
        'The request could not be fulfilled by the agent guidelines.';
    }

    // Scroll trace card into view
    traceCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // Tab toggling logic
  tabCampaigns.addEventListener('click', () => {
    tabCampaigns.classList.add('active');
    tabCampaigns.style.background = 'var(--primary)';
    tabCampaigns.style.color = 'white';

    tabRuns.classList.remove('active');
    tabRuns.style.background = 'transparent';
    tabRuns.style.color = 'var(--text-secondary)';

    campaignsTableContainer.classList.remove('hidden');
    runsTableContainer.classList.add('hidden');

    refreshCampaignsBtn.classList.remove('hidden');
    refreshRunsBtn.classList.add('hidden');
  });

  tabRuns.addEventListener('click', () => {
    tabRuns.classList.add('active');
    tabRuns.style.background = 'var(--primary)';
    tabRuns.style.color = 'white';

    tabCampaigns.classList.remove('active');
    tabCampaigns.style.background = 'transparent';
    tabCampaigns.style.color = 'var(--text-secondary)';

    runsTableContainer.classList.remove('hidden');
    campaignsTableContainer.classList.add('hidden');

    refreshRunsBtn.classList.remove('hidden');
    refreshCampaignsBtn.classList.add('hidden');

    loadRuns();
  });

  // Get color for channel
  function getChannelColor(channel) {
    if (!channel) return '#9333ea';
    switch (channel.toLowerCase()) {
      case 'push':
        return '#3b82f6';
      case 'email':
        return '#10b981';
      case 'sms':
        return '#f59e0b';
      default:
        return '#9333ea';
    }
  }

  // Update campaign preview card dynamically
  function updateCampaignPreview(campaign) {
    if (!campaign) {
      outcomeCampaign.classList.add('hidden');
      return;
    }
    outcomeCampaign.classList.remove('hidden');
    previewCampaignName.textContent = campaign.name || 'Unnamed Campaign';
    previewCampaignChannel.textContent = campaign.channel || 'Push';
    previewReach.textContent = `${(campaign.segment_size || 0).toLocaleString()} users`;

    const msg = campaign.message || {};
    previewSubject.textContent = msg.subject || msg.title || campaign.subject || campaign.title || '-';
    previewBody.textContent = msg.body || campaign.body || '-';

    const deepLink = msg.deep_link || campaign.image_url || campaign.link || '';
    if (deepLink) {
      previewLinkField.classList.remove('hidden');
      previewLink.href = deepLink;
      previewLink.textContent = deepLink;
    } else {
      previewLinkField.classList.add('hidden');
    }

    const offerField = document.getElementById('preview-offer-field') || (() => {
      const el = document.createElement('div');
      el.id = 'preview-offer-field';
      el.className = 'preview-field';
      previewLinkField.parentNode.insertBefore(el, previewLinkField);
      return el;
    })();

    if (campaign.offer) {
      offerField.classList.remove('hidden');
      offerField.innerHTML = `
        <span class="preview-label">Offer</span>
        <span class="preview-val font-medium text-accent">${escapeHtml(campaign.offer.value)} (${escapeHtml(campaign.offer.type)})</span>
      `;
    } else {
      offerField.classList.add('hidden');
    }
  }

  // Modal Display Campaign details
  function showCampaignDetail(campaign) {
    const msg = campaign.message || {};
    const subjectText = msg.subject || msg.title || campaign.subject || campaign.title || '-';
    const bodyText = msg.body || campaign.body || '-';
    const deepLink = msg.deep_link || campaign.image_url || campaign.link || '';
    const segmentObj = campaign.segment || campaign.segment_definition || {};

    modalBodyContent.innerHTML = `
            <div class="detail-section">
                <span class="detail-title">Campaign Name</span>
                <span class="detail-value font-medium" style="font-size: 1.1rem; color: #a855f7;">${escapeHtml(campaign.name || 'Unnamed')}</span>
            </div>
            
            <div class="form-row">
                <div class="detail-section">
                    <span class="detail-title">Campaign ID</span>
                    <span class="detail-value cell-mono select-all">${escapeHtml(campaign.campaign_id)}</span>
                </div>
                <div class="detail-section">
                    <span class="detail-title">Channel</span>
                    <span class="detail-value">
                        <span class="campaign-channel-badge" style="background-color: ${getChannelColor(campaign.channel)}">${escapeHtml(campaign.channel || 'push')}</span>
                    </span>
                </div>
            </div>

            <div class="form-row">
                <div class="detail-section">
                    <span class="detail-title">Reach</span>
                    <span class="detail-value font-medium">${(campaign.segment_size || 0).toLocaleString()} users</span>
                </div>
                <div class="detail-section">
                    <span class="detail-title">Created At</span>
                    <span class="detail-value">${campaign.created_at ? new Date(campaign.created_at).toLocaleString() : 'N/A'}</span>
                </div>
            </div>

            <div class="detail-section">
                <span class="detail-title">Subject / Title</span>
                <span class="detail-value font-medium">${escapeHtml(subjectText)}</span>
            </div>

            <div class="detail-section">
                <span class="detail-title">Message Body</span>
                <div style="background: rgba(0, 0, 0, 0.2); padding: 0.75rem; border-radius: 8px; border: 1px solid var(--border-color); font-size: 0.85rem; white-space: pre-wrap;">${escapeHtml(bodyText)}</div>
            </div>

            ${
              deepLink
                ? `
            <div class="detail-section">
                <span class="detail-title">Link</span>
                <span class="detail-value"><a href="${deepLink}" target="_blank" class="text-link">${escapeHtml(deepLink)}</a></span>
            </div>
            `
                : ''
            }

            ${
              campaign.offer
                ? `
            <div class="detail-section">
                <span class="detail-title">Offer Incentive</span>
                <div style="background: rgba(147, 51, 234, 0.05); padding: 0.75rem; border-radius: 8px; border: 1px solid rgba(147, 51, 234, 0.15); font-size: 0.85rem; line-height: 1.5;">
                  <strong>Type:</strong> ${escapeHtml(campaign.offer.type)}<br>
                  <strong>Value:</strong> ${escapeHtml(campaign.offer.value)}<br>
                  ${campaign.offer.expiry_days ? `<strong>Expiry:</strong> ${campaign.offer.expiry_days} days<br>` : ''}
                  ${campaign.offer.eligibility_note ? `<strong>Note:</strong> ${escapeHtml(campaign.offer.eligibility_note)}` : ''}
                </div>
            </div>
            `
                : ''
            }

            ${
              campaign.cited_guidelines && campaign.cited_guidelines.length > 0
                ? `
            <div class="detail-section">
                <span class="detail-title">Cited Guidelines</span>
                <div class="cited-guidelines-list" style="display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.25rem;">
                  ${campaign.cited_guidelines.map(docId => `
                    <span class="pill" style="color: var(--primary-hover); background: rgba(168, 85, 247, 0.1); border-color: rgba(168, 85, 247, 0.2); font-size: 0.7rem; font-weight: 600; padding: 0.25rem 0.6rem; border-radius: 6px; border: 1px solid rgba(168, 85, 247, 0.25);">
                      Doc #${docId}
                    </span>
                  `).join('')}
                </div>
            </div>
            `
                : ''
            }

            <div class="detail-section">
                <span class="detail-title">Target Segment Definition</span>
                <pre class="json-block">${JSON.stringify(segmentObj, null, 2)}</pre>
            </div>
        `;

    campaignModal.classList.remove('hidden');
    campaignModal.style.opacity = 1;
  }

  // Modal Close handlers
  function closeModal() {
    campaignModal.style.opacity = 0;
    setTimeout(() => {
      campaignModal.classList.add('hidden');
    }, 150);
  }

  closeModalBtn.addEventListener('click', closeModal);
  modalOverlay.addEventListener('click', closeModal);

  // Keypress listener for Escape key to close modal
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !campaignModal.classList.contains('hidden')) {
      closeModal();
    }
  });

  // Rich Structured Trace Details formatter
  function formatStepDetail(detail) {
    if (!detail || typeof detail !== 'object' || Object.keys(detail).length === 0) {
      return '';
    }

    let html = '<div class="step-detail-structured">';

    // 1. LLM Start specific fields
    if ('system_prompt' in detail && detail.system_prompt) {
      html += `
        <div class="detail-section-item">
          <div class="detail-section-label">System Prompt:</div>
          <div class="detail-section-value mono">${escapeHtml(detail.system_prompt)}</div>
        </div>
      `;
    }
    if ('recent_items' in detail && Array.isArray(detail.recent_items)) {
      html += `
        <div class="detail-section-item">
          <div class="detail-section-label">Recent Conversation History (last ${detail.recent_items.length}):</div>
          <div class="detail-recent-items">
            ${detail.recent_items.map((item, idx) => `
              <div class="recent-item">
                <span class="recent-item-index">Message #${idx + 1}</span>
                <pre class="recent-item-value">${escapeHtml(item)}</pre>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    // 2. LLM End specific fields
    if ('text' in detail && detail.text) {
      html += `
        <div class="detail-section-item">
          <div class="detail-section-label">Model Text Output:</div>
          <div class="detail-section-value">${escapeHtml(detail.text).replace(/\n/g, '<br>')}</div>
        </div>
      `;
    }
    if ('tool_calls' in detail && Array.isArray(detail.tool_calls)) {
      html += `
        <div class="detail-section-item">
          <div class="detail-section-label">Requested Tool Calls:</div>
          <div class="detail-tool-calls">
            ${detail.tool_calls.map(tc => `
              <div class="tool-call-item">
                <span class="tool-call-name">🔧 ${escapeHtml(tc.name)}</span>
                <pre class="tool-call-args">${escapeHtml(tc.arguments)}</pre>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    // 3. No terminal action specific fields
    if ('final_output' in detail && detail.final_output) {
      html += `
        <div class="detail-section-item">
          <div class="detail-section-label">Final Model Output:</div>
          <div class="detail-section-value error-text">${escapeHtml(detail.final_output).replace(/\n/g, '<br>')}</div>
        </div>
      `;
    }

    // 4. Token & response info
    const tokenInfo = [];
    if ('input_tokens' in detail) tokenInfo.push(`Input: ${detail.input_tokens}`);
    if ('output_tokens' in detail) tokenInfo.push(`Output: ${detail.output_tokens}`);
    if ('total_tokens' in detail) tokenInfo.push(`Total: ${detail.total_tokens}`);
    if ('new_items_count' in detail) tokenInfo.push(`Turns: ${detail.new_items_count}`);
    if (tokenInfo.length > 0) {
      html += `
        <div class="detail-section-item metadata-inline">
          <span class="detail-section-label">Stats:</span>
          <span class="detail-section-value font-mono">${tokenInfo.join(' | ')}</span>
        </div>
      `;
    }

    // 5. Fallback/Other detail keys (excluding already rendered ones)
    const processedKeys = [
      'system_prompt', 'recent_items', 'text', 'tool_calls', 
      'final_output', 'new_items_count', 'input_tokens', 
      'output_tokens', 'total_tokens', 'input_count', 'response_id'
    ];
    const otherKeys = Object.keys(detail).filter(k => !processedKeys.includes(k));
    if (otherKeys.length > 0) {
      const otherDetail = {};
      otherKeys.forEach(k => { otherDetail[k] = detail[k]; });
      html += `
        <div class="detail-section-item">
          <div class="detail-section-label">Additional Info:</div>
          <pre class="detail-section-value-json">${escapeHtml(JSON.stringify(otherDetail, null, 2))}</pre>
        </div>
      `;
    }

    html += '</div>';
    return html;
  }

  // Unified steps rendering function
  function renderSteps(steps) {
    stepsTimeline.innerHTML = '';
    if (!steps || steps.length === 0) {
      stepsTimeline.innerHTML =
        '<div class="td-placeholder" style="padding: 1rem; text-align: center; color: var(--text-muted);">No steps recorded for this trace.</div>';
      return;
    }

    steps.forEach((step) => {
      const stepCard = document.createElement('div');
      stepCard.className = `step-card ${step.kind || 'note'}`;
      if (step.status === 'error') {
        stepCard.classList.add('error');
      }

      let detailHtml = '';
      if (step.detail && Object.keys(step.detail).length > 0) {
        detailHtml = `
          <div class="step-detail hidden">${formatStepDetail(step.detail)}</div>
          <button type="button" class="btn btn-secondary btn-sm toggle-details-btn" style="margin-top: 0.25rem; align-self: flex-start; padding: 0.15rem 0.4rem; font-size: 0.65rem;">
              Show Details
          </button>
        `;
      }

      const timeText = step.latency_ms
        ? `${step.latency_ms.toLocaleString()} ms`
        : '';

      stepCard.innerHTML = `
        <div class="step-header">
            <span class="step-title">${escapeHtml(step.name)} (${step.kind})</span>
            <span class="step-meta">${timeText}</span>
        </div>
        <div class="step-summary">${escapeHtml(step.summary)}</div>
        ${detailHtml}
      `;

      const toggleBtn = stepCard.querySelector('.toggle-details-btn');
      if (toggleBtn) {
        const detailDiv = stepCard.querySelector('.step-detail');
        toggleBtn.addEventListener('click', () => {
          const isHidden = detailDiv.classList.contains('hidden');
          if (isHidden) {
            detailDiv.classList.remove('hidden');
            toggleBtn.textContent = 'Hide Details';
          } else {
            detailDiv.classList.add('hidden');
            toggleBtn.textContent = 'Show Details';
          }
        });
      }
      stepsTimeline.appendChild(stepCard);
    });
  }

  // Helper: Escape HTML to avoid XSS
  function escapeHtml(str) {
    if (!str) return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
});
