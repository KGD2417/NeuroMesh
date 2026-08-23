package dev.neuromesh.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items as gridItems
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Divider
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.neuromesh.data.Session
import dev.neuromesh.net.JobView
import dev.neuromesh.provider.ProviderState

// --- 1. setup ---------------------------------------------------------------

@Composable
fun SetupScreen(vm: AppViewModel) {
    val ui by vm.ui.collectAsState()
    var url by remember { mutableStateOf(ui.orchestrator) }
    var email by remember { mutableStateOf(ui.email) }
    var password by remember { mutableStateOf("") }
    var registering by remember { mutableStateOf(false) }

    Column(
        Modifier.fillMaxSize().padding(24.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.Center,
    ) {
        Text("NeuroMesh", fontSize = 34.sp, fontWeight = FontWeight.Bold)
        Text(
            "Sell your phone's idle NPU time. Or buy the fleet's.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(28.dp))

        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("Orchestrator") },
            supportingText = { Text("The phone running the server, e.g. 192.168.1.7:8000") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            value = email,
            onValueChange = { email = it },
            label = { Text("Email") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            value = password,
            onValueChange = { password = it },
            label = { Text("Password") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth(),
        )

        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Switch(checked = registering, onCheckedChange = { registering = it })
            Spacer(Modifier.size(8.dp))
            Text(if (registering) "Create a new account" else "Sign in")
        }

        Spacer(Modifier.height(16.dp))
        Button(
            onClick = {
                vm.setOrchestrator(url)
                vm.login(email.trim(), password, registering)
            },
            enabled = !ui.busy && email.isNotBlank() && password.length >= 8,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(if (registering) "Create account" else "Sign in")
        }
        Banner(vm)
    }
}

// --- 2. mode ----------------------------------------------------------------

@Composable
fun ModeScreen(vm: AppViewModel) {
    Column(
        Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Text("One app, two sides", fontSize = 26.sp, fontWeight = FontWeight.Bold)
        Text(
            "Same binary on every phone. Pick what this one does tonight.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(24.dp))

        ModeCard(
            title = "Provide compute",
            body = "Earn credits while charging, on Wi-Fi, screen off. Nothing runs " +
                "while you are using the phone.",
        ) { vm.chooseMode(Session.Mode.PROVIDER) }

        Spacer(Modifier.height(12.dp))

        ModeCard(
            title = "Submit a job",
            body = "Split a batch across the fleet and watch the shards land.",
        ) { vm.chooseMode(Session.Mode.CONSUMER) }

        Spacer(Modifier.height(24.dp))
        TextButton(onClick = { vm.logout() }) { Text("Sign out") }
        Banner(vm)
    }
}

@Composable
private fun ModeCard(title: String, body: String, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp)) {
            Text(title, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(6.dp))
            Text(body, style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.height(12.dp))
            Button(onClick = onClick) { Text("Choose") }
        }
    }
}

// --- 3. provider ------------------------------------------------------------

@Composable
fun ProviderScreen(vm: AppViewModel, onStart: () -> Unit, onStop: () -> Unit) {
    val ui by vm.ui.collectAsState()
    val provider by ProviderState.state.collectAsState()

    Column(
        Modifier.fillMaxSize().padding(20.dp).verticalScroll(rememberScrollState())
    ) {
        Header("Provider", ui.account?.balanceDisplay?.let { "$it credits" } ?: "")

        if (!ui.paired) {
            PairingCard(vm)
        } else {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(18.dp)) {
                    Text(provider.statusLine, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
                    provider.reason?.let {
                        Text(it, style = MaterialTheme.typography.bodyMedium)
                    }
                    Spacer(Modifier.height(14.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                        Stat("Tier", provider.tierLabel)
                        Stat("Backend", provider.backend)
                        Stat("Battery", "${provider.batteryPct}%")
                    }
                    Spacer(Modifier.height(12.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                        Stat("Shards", provider.shardsCompleted.toString())
                        Stat("Items", provider.itemsProcessed.toString())
                        Stat("Earned", "${provider.earnedMc} mC")
                    }
                    Spacer(Modifier.height(16.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Button(onClick = onStart, enabled = !provider.running) {
                            Text("Start providing")
                        }
                        OutlinedButton(onClick = onStop, enabled = provider.running) {
                            Text("Stop")
                        }
                    }
                }
            }

            Spacer(Modifier.height(12.dp))
            ConditionsCard(vm)

            Spacer(Modifier.height(12.dp))
            Text("Activity", fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(6.dp))
            if (provider.log.isEmpty()) {
                Text("Nothing yet.", style = MaterialTheme.typography.bodySmall)
            }
            provider.log.forEach {
                Text(it, fontFamily = FontFamily.Monospace, fontSize = 11.sp)
            }
        }

        Spacer(Modifier.height(20.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            TextButton(onClick = { vm.goTo(AppViewModel.Screen.MODE) }) { Text("Switch mode") }
            TextButton(onClick = { vm.refreshAccount() }) { Text("Refresh") }
            if (ui.paired) TextButton(onClick = { vm.unpair() }) { Text("Unpair") }
        }
        Banner(vm)
    }
}

@Composable
private fun PairingCard(vm: AppViewModel) {
    var code by remember { mutableStateOf("") }
    var name by remember { mutableStateOf(android.os.Build.MODEL) }
    val ui by vm.ui.collectAsState()

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp)) {
            Text("Join the fleet", fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
            Text(
                "Get a pairing code on the phone you signed in with, then type it here. " +
                    "The device key it returns is stored only on this phone.",
                style = MaterialTheme.typography.bodyMedium,
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = name, onValueChange = { name = it },
                label = { Text("This phone") }, singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = code, onValueChange = { code = it.uppercase() },
                label = { Text("Pairing code") }, singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(onClick = { vm.pairThisPhone(code, name) }, enabled = code.length >= 6) {
                    Text("Pair this phone")
                }
                OutlinedButton(onClick = { vm.mintPairingCode() }) { Text("Get a code") }
            }
            ui.pairingCode?.let {
                Spacer(Modifier.height(12.dp))
                Text("Code: $it", fontFamily = FontFamily.Monospace, fontSize = 22.sp)
                Text("Expires in 5 minutes.", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun ConditionsCard(vm: AppViewModel) {
    val provider by ProviderState.state.collectAsState()
    val state = remember(provider) { vm.eligibility.state() }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(18.dp)) {
            Text("Your phone comes first", fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(8.dp))
            Condition("Charging", state.charging)
            Condition("Unmetered Wi-Fi", state.wifi)
            Condition("Screen off", state.screenOff)
            Condition("Cool (${state.thermalStatus} ≤ ${vm.eligibility.thermalCeiling})",
                state.thermalStatus <= vm.eligibility.thermalCeiling)
        }
    }
}

@Composable
private fun Condition(label: String, ok: Boolean) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier.size(10.dp).clip(RoundedCornerShape(5.dp))
                .background(if (ok) MaterialTheme.colorScheme.primary else Color(0xFF555B77))
        )
        Spacer(Modifier.size(10.dp))
        Text(label, style = MaterialTheme.typography.bodyMedium)
    }
}

// --- 4. consumer ------------------------------------------------------------

@Composable
fun ConsumerScreen(vm: AppViewModel) {
    val ui by vm.ui.collectAsState()
    var model by remember(ui.models) { mutableStateOf(ui.models.firstOrNull()?.ref ?: "") }
    var items by remember { mutableStateOf("120") }
    var shardSize by remember { mutableStateOf("16") }

    Column(
        Modifier.fillMaxSize().padding(20.dp).verticalScroll(rememberScrollState())
    ) {
        Header("Submit a job", ui.account?.balanceDisplay?.let { "$it credits" } ?: "")

        Text("Model", fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(6.dp))
        ui.models.forEach { info ->
            FilterChip(
                selected = model == info.ref,
                onClick = { model = info.ref },
                label = { Text("${info.ref}  ·  ${info.minTierLabel}") },
                modifier = Modifier.padding(end = 8.dp),
            )
        }
        ui.models.firstOrNull { it.ref == model }?.let {
            Spacer(Modifier.height(6.dp))
            Text(it.description, style = MaterialTheme.typography.bodySmall)
            Text("${it.pricePerItemMc} mC per item", style = MaterialTheme.typography.bodySmall)
        }

        Spacer(Modifier.height(14.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedTextField(
                value = items, onValueChange = { items = it.filter(Char::isDigit) },
                label = { Text("Items") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                modifier = Modifier.weight(1f),
            )
            OutlinedTextField(
                value = shardSize, onValueChange = { shardSize = it.filter(Char::isDigit) },
                label = { Text("Shard size") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                modifier = Modifier.weight(1f),
            )
        }

        val n = items.toIntOrNull() ?: 0
        val s = shardSize.toIntOrNull() ?: 1
        Spacer(Modifier.height(6.dp))
        Text(
            if (n > 0 && s > 0) "${(n + s - 1) / s} shards across the fleet" else "—",
            style = MaterialTheme.typography.bodySmall,
        )

        Spacer(Modifier.height(14.dp))
        Button(
            onClick = { vm.submitJob(model, n, s) },
            enabled = !ui.busy && model.isNotBlank() && n > 0 && s > 0,
            modifier = Modifier.fillMaxWidth(),
        ) { Text("Submit job") }

        Spacer(Modifier.height(18.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            TextButton(onClick = { vm.goTo(AppViewModel.Screen.MODE) }) { Text("Switch mode") }
            TextButton(onClick = { vm.refreshConsumer() }) { Text("Refresh") }
            ui.activeJob?.let {
                TextButton(onClick = { vm.goTo(AppViewModel.Screen.JOB) }) { Text("Last job") }
            }
        }
        Banner(vm)
    }
}

// --- 5. job watch -----------------------------------------------------------

@Composable
fun JobScreen(vm: AppViewModel) {
    val ui by vm.ui.collectAsState()
    val job = ui.activeJob

    Column(Modifier.fillMaxSize().padding(20.dp)) {
        Header("Job", job?.status.orEmpty())
        if (job == null) {
            Text("No job yet.")
            return@Column
        }

        val done = job.shardsDone
        val total = job.shardCount.coerceAtLeast(1)
        LinearProgressIndicator(
            progress = { done.toFloat() / total },
            modifier = Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp)),
        )
        Spacer(Modifier.height(8.dp))
        Text(
            "$done / ${job.shardCount} shards · ${job.shardsClaimed} in flight · " +
                "${job.shardsFailed} failed · ${job.costMc} mC escrowed",
            style = MaterialTheme.typography.bodyMedium,
        )

        Spacer(Modifier.height(14.dp))
        ShardGrid(job)

        Spacer(Modifier.height(14.dp))
        ui.result?.let {
            Text("Result assembled", fontWeight = FontWeight.SemiBold)
            Text(
                "${it.outputs.size} outputs in order" +
                    if (it.failedShards.isEmpty()) "" else " · failed shards ${it.failedShards}",
                style = MaterialTheme.typography.bodySmall,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                it.outputs.firstOrNull()?.toString()?.take(160).orEmpty(),
                fontFamily = FontFamily.Monospace, fontSize = 10.sp,
            )
            Spacer(Modifier.height(10.dp))
        }

        Text("Live", fontWeight = FontWeight.SemiBold)
        Divider(Modifier.padding(vertical = 6.dp))
        LazyColumn(Modifier.weight(1f)) {
            items(ui.liveEvents.reversed()) { event ->
                Text(
                    buildString {
                        append(event.type.padEnd(16))
                        event.index?.let { append(" shard #$it") }
                        event.deviceId?.let { append(" · ${it.take(8)}") }
                    },
                    fontFamily = FontFamily.Monospace, fontSize = 11.sp,
                )
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            TextButton(onClick = { vm.goTo(AppViewModel.Screen.CONSUMER) }) { Text("Back") }
            TextButton(onClick = { vm.refreshJob() }) { Text("Refresh") }
            if (job.status == "queued" || job.status == "running") {
                TextButton(onClick = { vm.cancelJob() }) { Text("Cancel") }
            }
        }
        Banner(vm)
    }
}

/** One cell per shard: the whole point of the demo, visible at a glance. */
@Composable
private fun ShardGrid(job: JobView) {
    LazyVerticalGrid(
        columns = GridCells.Adaptive(minSize = 34.dp),
        modifier = Modifier.fillMaxWidth().height(120.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        gridItems(job.shards) { shard ->
            Box(
                Modifier
                    .size(30.dp)
                    .clip(RoundedCornerShape(6.dp))
                    .background(shardColor(shard.state)),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    shard.index.toString(),
                    fontSize = 9.sp,
                    color = MaterialTheme.colorScheme.onPrimary,
                )
            }
        }
    }
}

@Composable
private fun shardColor(state: String): Color = when (state) {
    "done" -> MaterialTheme.colorScheme.primary
    "claimed" -> MaterialTheme.colorScheme.secondary
    "failed" -> MaterialTheme.colorScheme.error
    "cancelled" -> Color(0xFF6B7280)
    else -> Color(0xFF2A3252)
}

// --- shared bits ------------------------------------------------------------

@Composable
private fun Header(title: String, trailing: String) {
    Row(
        Modifier.fillMaxWidth().padding(bottom = 14.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, fontSize = 24.sp, fontWeight = FontWeight.Bold)
        Text(trailing, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun Stat(label: String, value: String) {
    Column {
        Text(label, style = MaterialTheme.typography.labelSmall)
        Text(value, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun Banner(vm: AppViewModel) {
    val ui by vm.ui.collectAsState()
    if (ui.busy) {
        Spacer(Modifier.height(12.dp))
        CircularProgressIndicator(Modifier.size(20.dp))
    }
    (ui.error ?: ui.notice)?.let { message ->
        Spacer(Modifier.height(12.dp))
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(12.dp)) {
                Text(
                    message,
                    color = if (ui.error != null) MaterialTheme.colorScheme.error
                    else MaterialTheme.colorScheme.onSurface,
                    style = MaterialTheme.typography.bodyMedium,
                )
                TextButton(onClick = { vm.dismissError() }) { Text("Dismiss") }
            }
        }
    }
}
