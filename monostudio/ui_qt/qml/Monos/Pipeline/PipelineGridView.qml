import QtQuick
import Monos.Pipeline 1.0

GridView {
    id: grid
    property int cardWidth: 200
    property int cardGap: 16

    cellWidth: cardWidth + cardGap
    cellHeight: Math.round(cardWidth * 9 / 16) + 72 + cardGap
    clip: true

    delegate: PipelineCard {
        width: grid.cardWidth
        height: grid.cellHeight - grid.cardGap
        displayName: model.displayName
        statusLabel: model.statusLabel
        statusColor: model.statusColor
        alertLabel: model.alertLabel
        dccLabels: model.dccNames ? model.dccNames.split(",") : []
        thumbSource: model.thumbSource
        thumbOpacity: model.thumbOpacity
        selected: GridView.isCurrentItem
        hovered: grid.currentIndex === index
    }
}
