import { useEffect, useRef, useState } from "react";
import { Bookmark, ChevronLeft, ChevronRight, Pencil, Plus, Search } from "lucide-react";
import {
  connectionEndpointLabel,
  deviceProfileDisplayName,
  deviceTypeDisplayName,
  protocolDisplayName,
} from "../../lib/connection.js";

const DEVICE_PANEL_MIN_WIDTH = 220;
const DEVICE_PANEL_MAX_WIDTH = 420;
const DEVICE_PANEL_DEFAULT_WIDTH = 280;
const DEVICE_PANEL_COLLAPSED_WIDTH = 1;
const DEVICE_PANEL_WIDTH_KEY = "auditbox-device-panel-width";

function clampPanelWidth(width) {
  return Math.min(DEVICE_PANEL_MAX_WIDTH, Math.max(DEVICE_PANEL_MIN_WIDTH, width));
}

function readStoredPanelWidth() {
  try {
    const stored = Number(window.localStorage.getItem(DEVICE_PANEL_WIDTH_KEY));
    return Number.isFinite(stored) && stored > 0
      ? clampPanelWidth(stored)
      : DEVICE_PANEL_DEFAULT_WIDTH;
  } catch {
    return DEVICE_PANEL_DEFAULT_WIDTH;
  }
}

const STATUS_LABELS = {
  online: "在线",
  offline: "离线",
  probing: "探测中",
};

export function BookmarkPanel({
  selectedDeviceId,
  targets,
  search,
  loading,
  error,
  total,
  onSearchChange,
  onSelect,
  onEdit,
  onAdd,
}) {
  const [panelWidth, setPanelWidth] = useState(readStoredPanelWidth);
  // A fresh console starts expanded; only the user's preferred width persists.
  const [collapsed, setCollapsed] = useState(false);
  const dragStateRef = useRef(null);

  useEffect(() => {
    const appliedWidth = collapsed ? DEVICE_PANEL_COLLAPSED_WIDTH : panelWidth;
    document.documentElement.style.setProperty("--device-panel-width", `${appliedWidth}px`);
    try {
      window.localStorage.setItem(DEVICE_PANEL_WIDTH_KEY, String(panelWidth));
    } catch {
      // The layout still works when storage is unavailable (for example, private browsing policies).
    }

    return () => {
      document.documentElement.style.removeProperty("--device-panel-width");
    };
  }, [collapsed, panelWidth]);

  function startResize(event) {
    if (collapsed || event.button !== 0) return;
    event.preventDefault();
    dragStateRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: panelWidth,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function resizePanel(event) {
    const dragState = dragStateRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) return;
    setPanelWidth(clampPanelWidth(dragState.startWidth + event.clientX - dragState.startX));
  }

  function stopResize(event) {
    if (dragStateRef.current?.pointerId !== event.pointerId) return;
    dragStateRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function resizeWithKeyboard(event) {
    if (collapsed || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const direction = event.key === "ArrowLeft" ? -1 : 1;
    setPanelWidth((width) => clampPanelWidth(width + direction * 16));
  }

  return (
    <aside className={`side-panel left-panel ${collapsed ? "collapsed" : ""}`}>
      <div className="panel-header device-panel-header">
        <div className="device-panel-title">
          <h2>设备列表</h2>
          <small>{total} 台</small>
        </div>
        <div className="device-panel-actions">
          <button className="icon-button" type="button" title="新建安全连接" aria-label="新建安全连接" onClick={onAdd}>
            <Plus size={18} />
          </button>
        </div>
      </div>
      <button
        className="device-panel-edge-toggle"
        type="button"
        title={collapsed ? "展开设备列表" : "收起设备列表"}
        aria-label={collapsed ? "展开设备列表" : "收起设备列表"}
        aria-expanded={!collapsed}
        onClick={() => setCollapsed((value) => !value)}
      >
        {collapsed ? <ChevronRight size={17} /> : <ChevronLeft size={17} />}
      </button>
      <div className="device-panel-content" aria-hidden={collapsed}>
        <div className="search-box">
          <Search size={16} />
          <input
            placeholder="搜索设备"
            value={search}
            tabIndex={collapsed ? -1 : 0}
            onChange={(event) => onSearchChange(event.target.value)}
          />
        </div>
        <div className="bookmark-list">
          {loading ? <div className="bookmark-state">加载设备...</div> : null}
          {error ? <div className="bookmark-state error">{error}</div> : null}
          {!loading && !error && targets.length === 0 ? (
            <div className="bookmark-state">暂无已管理设备</div>
          ) : null}
          {targets.map((item) => {
            const hasRemark = Boolean(item.remark.trim());
            const primaryName = hasRemark ? item.remark.trim() : connectionEndpointLabel(item);
            const statusLabel = STATUS_LABELS[item.status] || STATUS_LABELS.probing;
            return (
              <div
                className={`bookmark-card ${selectedDeviceId === item.id ? "active" : ""}`}
                key={item.id}
              >
                <button
                  className="bookmark-card-select"
                  type="button"
                  tabIndex={collapsed ? -1 : 0}
                  onClick={() => onSelect(item)}
                  aria-label={`连接 ${primaryName}`}
                >
                  <span className={`status-dot ${item.status}`} title={statusLabel} aria-label={statusLabel} />
                  <span className="bookmark-main">
                    <strong>{primaryName}</strong>
                    <small>
                      {item.host} · {deviceTypeDisplayName(item.deviceType)} · {deviceProfileDisplayName(item.deviceProfile)} · {protocolDisplayName(item.protocol)}
                    </small>
                  </span>
                </button>
                <button
                  className="bookmark-edit-button"
                  type="button"
                  tabIndex={collapsed ? -1 : 0}
                  title={`编辑 ${primaryName}`}
                  aria-label={`编辑 ${primaryName}`}
                  onClick={() => onEdit(item)}
                >
                  <Pencil size={15} />
                </button>
              </div>
            );
          })}
        </div>
      </div>
      <div className="audit-strip device-panel-footer">
        <Bookmark size={16} />
        <span>已管理设备</span>
        <strong>{total} 台</strong>
      </div>
      <div
        className="device-panel-resizer"
        role="separator"
        aria-label="调整设备列表宽度"
        aria-orientation="vertical"
        aria-valuemin={DEVICE_PANEL_MIN_WIDTH}
        aria-valuemax={DEVICE_PANEL_MAX_WIDTH}
        aria-valuenow={panelWidth}
        tabIndex={collapsed ? -1 : 0}
        onPointerDown={startResize}
        onPointerMove={resizePanel}
        onPointerUp={stopResize}
        onPointerCancel={stopResize}
        onKeyDown={resizeWithKeyboard}
      />
    </aside>
  );
}
