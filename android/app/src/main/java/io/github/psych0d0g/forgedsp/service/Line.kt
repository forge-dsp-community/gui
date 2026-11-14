package io.github.psych0d0g.forgedsp.service

/**
 * One line of the Forge (as src/lib/protocol/answer.ts): <.1 reg=value (answer), *.1 reg ok (set
 * done), !.1 reg text (error), #.1 reg=value (event), >.1 … (echo).
 */
data class Line(val kind: Char, val device: Int, val register: String, val value: String?, val raw: String) {
    val isEvent get() = kind == '#'
    val isError get() = kind == '!'

    companion object {
        private val RE = Regex("""^\s*([<>*!#])\.(\d+) (\S*?)(?:=(.*)|\s+(.*))?$""")

        fun parse(text: String): Line? {
            val raw = text.trimEnd('\r', '\n')
            val m = RE.matchEntire(raw) ?: return null
            return Line(m.groupValues[1][0], m.groupValues[2].toInt(), m.groupValues[3], m.groups[4]?.value, raw)
        }

        /** register of a request line "get.1 reg" / "set.1 reg=value", null if it is none */
        fun requestRegister(line: String): String? =
            Regex("""^\s*(?:get|set)\.\w+ ([A-Za-z0-9.*]+)""").find(line)?.groupValues?.get(1)

        /** modc.N=[enable,type,FLAGS,e1,e2,e3,e4,SETS] -> (N, enable, type, events) */
        fun modc(l: Line): Modc? {
            val n = l.register.removePrefix("modc.").toIntOrNull() ?: return null
            val p = l.value?.trim()?.removePrefix("[")?.removeSuffix("]")?.split(',') ?: return null
            if (p.size < 8) return null
            return Modc(n, p[0].trim().toIntOrNull() ?: 0, p[1].trim().toIntOrNull() ?: 0, (3..6).map { p[it].trim().toIntOrNull() ?: 0 })
        }
    }
}

data class Modc(val n: Int, val enable: Int, val type: Int, val events: List<Int>)

/** the master volume: the first enabled volume block (type 16) with event 1, as the GUI (master.ts) */
fun masterEvents(blocks: List<Modc>): List<Int>? {
    val b = blocks.sortedBy { it.n }.firstOrNull { it.enable != 0 && it.type == 16 && it.events[0] > 0 } ?: return null
    return listOf(b.events[0], if (b.events[1] > 0) b.events[1] else b.events[0]).distinct()
}

/** master in dB (0 = 0 dB, up to -127.5; event value 255 = 0 dB, 0.5 dB per step) <-> event value */
fun masterValue(db: Double): Int = (255 + Math.round(2 * db)).toInt().coerceIn(0, 255)
fun masterDb(value: Int): Double = (value.coerceIn(0, 255) - 255) / 2.0
