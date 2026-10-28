import React, { useEffect, useImperativeHandle, useRef, useState } from "react";

export function StatusPill({ icon: Icon, label, tone = "neutral" }) {
  return (
    <span className={`status-pill ${tone}`}>
      <Icon size={14} />
      {label}
    </span>
  );
}
