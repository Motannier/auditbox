// Older model/backend results may still carry lock flags. Reviews only inform
// the operator; they must not control terminal input or one-time confirmation.
export function staleReviewUpdate(review, previous = {}) {
  const revision = review.review_revision || 0;
  const prior = previous.review_revision || 0;
  return revision < prior || (revision === prior && revision > 0 && review.status === "queued"
    && ["ready", "error"].includes(previous.status));
}

export function currentCommandReview(review = {}) {
  const history = review.history || [];
  const seq = history.at(-1)?.seq;
  if (["ready", "locked"].includes(review.status)) {
    if (seq != null && review.current?.seq === seq) return review.current;
    if (history.length === 1 && review.reviewed_through_seq === seq) {
      return { seq, risk: review.risk || "unknown", reason: review.reason || "" };
    }
  }
  return { seq, risk: "unknown", reason: "当前命令风险未判定" };
}

export function advisoryReview(review = {}) {
  return {
    ...review,
    status: review.status === "locked" ? "ready" : review.status,
    lock: false,
    locked: false,
    lock_policy: { allow_lock: false, code: "advisory_only" },
  };
}
