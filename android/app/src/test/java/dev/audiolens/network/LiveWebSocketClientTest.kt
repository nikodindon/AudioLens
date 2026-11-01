package dev.audiolens.network

import java.net.ServerSocket
import java.security.MessageDigest
import java.util.Base64
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveWebSocketClientTest {
    @Test fun stopReceivesFinalBeforeClose() {
        ServerSocket(0).use { server ->
            server.soTimeout = 5000
            val allowClose = CountDownLatch(1)
            val stopSeen = CountDownLatch(1)
            val opened = CountDownLatch(1)
            val finalSeen = CountDownLatch(1)
            val completed = CountDownLatch(1)
            var success = false
            var sentStop = ""
            val worker = thread(isDaemon = true) {
                server.accept().use { peer ->
                    peer.soTimeout = 5000
                    val input = peer.getInputStream()
                    val output = peer.getOutputStream()
                    val request = StringBuilder()
                    while (!request.endsWith("\r\n\r\n")) request.append(input.read().toChar())
                    val key = request.toString().lineSequence().first { it.startsWith("Sec-WebSocket-Key:", true) }.substringAfter(':').trim()
                    val accept = Base64.getEncoder().encodeToString(MessageDigest.getInstance("SHA-1").digest((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").toByteArray()))
                    output.write("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: $accept\r\n\r\n".toByteArray())
                    output.flush()
                    input.read() // opcode
                    val length = input.read() and 0x7f
                    val mask = input.readNBytes(4)
                    val data = input.readNBytes(length)
                    sentStop = String(ByteArray(length) { (data[it].toInt() xor mask[it % 4].toInt()).toByte() })
                    stopSeen.countDown()
                    val final = "{\"type\":\"final\",\"text_src\":\"hello\"}".toByteArray()
                    output.write(byteArrayOf(0x81.toByte(), final.size.toByte()))
                    output.write(final)
                    output.flush()
                    if (allowClose.await(5, TimeUnit.SECONDS)) {
                        output.write(byteArrayOf(0x88.toByte(), 2, 3, 0xe8.toByte()))
                        output.flush()
                        Thread.sleep(100)
                    }
                }
            }
            val client = LiveWebSocketClient("http://127.0.0.1:${server.localPort}", "test", { finalSeen.countDown() }, { opened.countDown() })
            client.connect()
            assertTrue(opened.await(5, TimeUnit.SECONDS))
            client.finish { success = it; completed.countDown() }
            try {
                assertTrue(stopSeen.await(5, TimeUnit.SECONDS))
                assertEquals("{\"type\":\"stop\"}", sentStop)
                assertTrue(finalSeen.await(5, TimeUnit.SECONDS))
                assertFalse(completed.await(100, TimeUnit.MILLISECONDS))
            } finally {
                allowClose.countDown()
            }
            assertTrue(completed.await(5, TimeUnit.SECONDS))
            assertTrue(success)
            worker.join(5000)
        }
    }
}
