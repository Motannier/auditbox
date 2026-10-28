import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { IPV4_INPUT_PATTERN, normalizeIPv4Input } from "../../lib/ipv4.js";

export function GuideField({ field, value, onChange, disabled }) {
  const isIpField = field.kind === "ip";

  function updateValue(nextValue) {
    onChange(isIpField ? normalizeIPv4Input(nextValue, value) : nextValue);
  }

  return (
    <label>
      <span>{field.label}</span>
      {field.kind === "select" ? (
        <select aria-label={field.label} value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
          {field.options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
        </select>
      ) : <input
        type={field.secret ? "password" : "text"}
        inputMode={isIpField ? "decimal" : field.inputMode}
        pattern={isIpField ? IPV4_INPUT_PATTERN : undefined}
        title={isIpField ? "请输入有效的完整 IPv4 地址" : undefined}
        value={value}
        disabled={disabled}
        onChange={(event) => updateValue(event.target.value)}
        placeholder={`请填写${field.label}`}
      />}
    </label>
  );
}
