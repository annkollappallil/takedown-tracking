/**
 * Social Media Takedown Accountability Platform - Frontend Application
 */

document.addEventListener('DOMContentLoaded', () => {
  initThemeToggle();
  fetchAndRenderScoreboard();
  initPlatformSearch();
});

// Theme switcher
function initThemeToggle() {
  const toggleBtn = document.getElementById('theme-toggle');
  const body = document.body;
  const savedTheme = localStorage.getItem('ttp-theme');

  if (savedTheme === 'light') {
    body.classList.remove('dark-theme');
    body.classList.add('light-theme');
  }

  toggleBtn.addEventListener('click', () => {
    if (body.classList.contains('dark-theme')) {
      body.classList.remove('dark-theme');
      body.classList.add('light-theme');
      localStorage.setItem('ttp-theme', 'light');
    } else {
      body.classList.remove('light-theme');
      body.classList.add('dark-theme');
      localStorage.setItem('ttp-theme', 'dark');
    }
  });
}

let cachedScoreboardData = null;

// Fetch and render data
async function fetchAndRenderScoreboard() {
  try {
    const res = await fetch('/api/scoreboard');
    if (!res.ok) {
      throw new Error(`API responded with HTTP status ${res.status}`);
    }
    const data = await res.json();
    cachedScoreboardData = data;
    renderDashboard(data);
  } catch (err) {
    console.error('Failed to load scoreboard metrics:', err);
    showErrorState('Unable to load scoreboard data. Verify that the backend server is active.');
  }
}

function renderDashboard(data) {
  const meta = data.metadata;
  const perf = data.overall_performance;

  // 1. Render Demonstration Notice Banner
  const banner = document.getElementById('demo-banner');
  const bannerText = document.getElementById('banner-text');
  if (meta.is_demonstration_mode) {
    banner.style.display = 'block';
    bannerText.innerHTML = meta.disclaimer;
  } else {
    banner.style.display = 'none';
  }

  // 2. Render Meta Summary
  const startStr = meta.measurement_period_start.split('T')[0];
  const endStr = meta.measurement_period_end.split('T')[0];
  document.getElementById('meta-period').textContent = `${startStr} to ${endStr}`;
  document.getElementById('meta-total-requests').textContent = meta.total_requests_analyzed.toLocaleString();
  document.getElementById('meta-incomplete-requests').textContent = perf.incomplete_police_requests_count !== null ? perf.incomplete_police_requests_count.toLocaleString() : '[Protected < k]';
  document.getElementById('meta-invalid-records').textContent = perf.invalid_records_count.toLocaleString();

  // 3. Render Metric Cards with Right-Censoring Clarity
  const medianText = perf.median_removal_censored ? (perf.median_removal_display || '> measurement window') : (perf.median_removal_hours !== null ? perf.median_removal_hours.toFixed(1) : 'N/A');
  const p90Text = perf.p90_removal_censored ? (perf.p90_removal_display || '> measurement window') : (perf.p90_removal_hours !== null ? perf.p90_removal_hours.toFixed(1) : 'N/A');

  renderValue('metric-median-removal', medianText);
  renderValue('metric-p90-removal', p90Text);
  renderValue('metric-deadline-compliance', perf.deadline_compliance_rate_pct !== null ? perf.deadline_compliance_rate_pct.toFixed(1) : 'N/A');
  renderValue('metric-still-available', perf.still_available_rate_pct !== null ? perf.still_available_rate_pct.toFixed(1) : '0.0');
  renderValue('metric-median-data', perf.median_data_response_hours !== null ? perf.median_data_response_hours.toFixed(1) : 'N/A');
  renderValue('metric-incomplete-count', perf.incomplete_police_requests_count !== null ? perf.incomplete_police_requests_count.toLocaleString() : '[Suppressed < k]');

  // Compliance Progress Bar
  const progressBar = document.getElementById('compliance-progress-bar');
  if (perf.deadline_compliance_rate_pct !== null) {
    progressBar.style.width = `${Math.min(100, Math.max(0, perf.deadline_compliance_rate_pct))}%`;
  }

  // Benchmark indicators
  const medianIndicator = document.getElementById('median-benchmark-status');
  if (perf.median_removal_censored) {
    medianIndicator.textContent = '⚠ Overdue (Censored)';
    medianIndicator.className = 'status-indicator warn';
  } else if (perf.median_removal_hours !== null) {
    if (perf.median_removal_hours <= 24.0) {
      medianIndicator.textContent = '✓ Within 24h SLA';
      medianIndicator.className = 'status-indicator pass';
    } else {
      medianIndicator.textContent = '⚠ Exceeds 24h SLA';
      medianIndicator.className = 'status-indicator warn';
    }
  }

  const dataIndicator = document.getElementById('data-sla-status');
  if (perf.median_data_response_hours !== null) {
    if (perf.median_data_response_hours <= 72.0) {
      dataIndicator.textContent = '✓ Within 72h SLA';
      dataIndicator.className = 'status-indicator pass';
    } else {
      dataIndicator.textContent = '⚠ Overdue SLA';
      dataIndicator.className = 'status-indicator warn';
    }
  }

  // 4. Render Platforms Table
  renderPlatformsTable(data.platform_breakdown);

  // 5. Render Categories Table
  renderCategoriesTable(data.category_breakdown);
}

function renderValue(elementId, text) {
  const el = document.getElementById(elementId);
  if (el) el.textContent = text;
}

function renderPlatformsTable(platforms) {
  const tbody = document.getElementById('platforms-table-body');
  if (!tbody) return;

  if (!platforms || platforms.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" class="text-center">No platform data available.</td></tr>';
    return;
  }

  tbody.innerHTML = platforms.map(p => {
    if (p.is_suppressed) {
      return `
        <tr>
          <td><strong>${escapeHtml(p.platform)}</strong></td>
          <td><span class="suppressed-badge" title="Cohort is below the publication threshold.">Suppressed</span></td>
          <td colspan="7" class="text-muted"><em>Protected cohort &lt; 10 suppressed under k-anonymity privacy policy.</em></td>
        </tr>
      `;
    }

    const medDisp = p.median_removal_censored ? `<span class="badge-neutral" title="Unresolved cases exceed median rank">${escapeHtml(p.median_removal_display || '> window')}</span>` : (p.median_removal_hours !== null ? p.median_removal_hours.toFixed(1) : '--');
    const p90Disp = p.p90_removal_censored ? `<span class="badge-neutral" title="Unresolved cases exceed p90 rank">${escapeHtml(p.p90_removal_display || '> window')}</span>` : (p.p90_removal_hours !== null ? p.p90_removal_hours.toFixed(1) : '--');
    const compClass = (p.deadline_compliance_rate_pct !== null && p.deadline_compliance_rate_pct >= 80) ? 'status-indicator pass' : 'status-indicator warn';
    const rejectDisp = p.rejections_count !== null ? `${p.rejections_count} (${p.rejection_rate_pct !== null ? p.rejection_rate_pct.toFixed(1) : '0'}%)` : (p.rejection_rate_pct !== null ? `${p.rejection_rate_pct.toFixed(1)}%` : '--');

    return `
      <tr>
        <td><strong>${escapeHtml(p.platform)}</strong></td>
        <td><span class="public-badge">Audited</span></td>
        <td>${p.sample_size !== null ? p.sample_size : '--'}</td>
        <td>${medDisp}</td>
        <td>${p90Disp}</td>
        <td class="${compClass}">${p.deadline_compliance_rate_pct !== null ? p.deadline_compliance_rate_pct.toFixed(1) + '%' : 'N/A'}</td>
        <td>${p.still_available_rate_pct !== null ? p.still_available_rate_pct.toFixed(1) + '%' : '0.0%'}</td>
        <td>${rejectDisp}</td>
        <td>${p.median_data_response_hours !== null ? p.median_data_response_hours.toFixed(1) + ' hrs' : 'N/A'}</td>
      </tr>
    `;
  }).join('');
}

function renderCategoriesTable(categories) {
  const tbody = document.getElementById('categories-table-body');
  if (!tbody) return;

  if (!categories || categories.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-center">No category data available.</td></tr>';
    return;
  }

  tbody.innerHTML = categories.map(c => {
    if (c.is_suppressed) {
      return `
        <tr>
          <td><strong>${escapeHtml(c.category)}</strong></td>
          <td><span class="suppressed-badge" title="Cohort is below the publication threshold.">Suppressed</span></td>
          <td colspan="4" class="text-muted"><em>Protected cohort &lt; 10 cases suppressed under k-anonymity privacy policy.</em></td>
        </tr>
      `;
    }

    const medDisp = c.median_removal_censored ? `<span class="badge-neutral">${escapeHtml(c.median_removal_display || '> window')}</span>` : (c.median_removal_hours !== null ? c.median_removal_hours.toFixed(1) : '--');
    const p90Disp = c.p90_removal_censored ? `<span class="badge-neutral">${escapeHtml(c.p90_removal_display || '> window')}</span>` : (c.p90_removal_hours !== null ? c.p90_removal_hours.toFixed(1) : '--');

    return `
      <tr>
        <td><strong>${escapeHtml(c.category)}</strong></td>
        <td><span class="public-badge">Audited</span></td>
        <td>${c.sample_size !== null ? c.sample_size : '--'}</td>
        <td>${medDisp}</td>
        <td>${p90Disp}</td>
        <td>${c.deadline_compliance_rate_pct !== null ? c.deadline_compliance_rate_pct.toFixed(1) + '%' : 'N/A'}</td>
      </tr>
    `;
  }).join('');
}

function initPlatformSearch() {
  const searchInput = document.getElementById('platform-search');
  if (!searchInput) return;

  searchInput.addEventListener('input', (e) => {
    const query = e.target.value.toLowerCase().trim();
    if (!cachedScoreboardData) return;

    const filtered = cachedScoreboardData.platform_breakdown.filter(p => 
      p.platform.toLowerCase().includes(query)
    );
    renderPlatformsTable(filtered);
  });
}

function showErrorState(message) {
  const tbody = document.getElementById('platforms-table-body');
  if (tbody) {
    tbody.innerHTML = `<tr><td colspan="9" class="text-center" style="color: var(--accent-rose);">${escapeHtml(message)}</td></tr>`;
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
