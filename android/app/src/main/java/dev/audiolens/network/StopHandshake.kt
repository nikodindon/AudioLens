package dev.audiolens.network

/** Completes a stop once, after the peer closes or an error/timeout occurs. */
internal class StopHandshake {
    private var callback: ((Boolean) -> Unit)? = null
    private var completed = false

    @Synchronized
    fun begin(onComplete: (Boolean) -> Unit): Boolean {
        if (callback != null || completed) return false
        callback = onComplete
        return true
    }

    fun complete(success: Boolean) {
        val notify: ((Boolean) -> Unit)?
        synchronized(this) {
            if (completed || callback == null) return
            completed = true
            notify = callback
            callback = null
        }
        notify?.invoke(success)
    }
}
