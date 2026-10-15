package com.kmsp.tracker.quicksettings

import android.content.Intent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import android.widget.Toast
import com.kmsp.tracker.sync.ExactAlarmReceiver
import com.kmsp.tracker.sync.ReplaySyncService

class SyncTileService : TileService() {

    override fun onClick() {
        super.onClick()
        val intent = Intent(this, ReplaySyncService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
        ExactAlarmReceiver.scheduleNextSync(this)
        Toast.makeText(this, "KMSP Desk Sync Triggered", Toast.LENGTH_SHORT).show()
    }

    override fun onStartListening() {
        super.onStartListening()
        qsTile?.state = Tile.STATE_ACTIVE
        qsTile?.label = "KMSP Sync"
        qsTile?.updateTile()
    }
}

