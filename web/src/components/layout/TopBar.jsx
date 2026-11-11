import { useState } from "react";
import { Download, LogOut, Settings, Shield } from "lucide-react";
import { operatorDisplayName, PRODUCT_NAME } from "../../constants/branding.js";
import { fetchOperationLogExport } from "../../services/auditService.js";
import { NetworkSettingsDialog } from "../settings/NetworkSettingsDialog.jsx";

export function TopBar({ user, isSuperAdmin, onLogout }) {
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);

  async function handleExport() {
    setExporting(true);
    setExportError("");
    try {
      const { blob, filename } = await fetchOperationLogExport();
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
    } catch (err) {
      setExportError(err.message || "操作日志导出失败");
    } finally {
      setExporting(false);
    }
  }

  return (
    <>
    <header className="topbar">
      <div className="topbar-left">
        <div className="mini-brand">
          <Shield size={20} />
        </div>
        <div>
          <strong>{PRODUCT_NAME}</strong>
        </div>
      </div>
      <div className="topbar-actions">
        <button className="ghost-button" type="button" onClick={() => setSettingsOpen(true)}>
          <Settings size={17} />设置
        </button>
        {exportError ? <span className="topbar-export-error" role="alert">{exportError}</span> : null}
        {isSuperAdmin ? (
          <button
            className="ghost-button topbar-export-button"
            type="button"
            onClick={handleExport}
            disabled={exporting}
          >
            <Download size={17} />
            {exporting ? "正在导出" : "导出操作日志"}
          </button>
        ) : null}
        <button className="ghost-button" type="button" onClick={onLogout}>
          <LogOut size={17} />
          {operatorDisplayName(user)}
        </button>
      </div>
    </header>
    {settingsOpen ? <NetworkSettingsDialog onClose={() => setSettingsOpen(false)} /> : null}
    </>
  );
}
