# Incremental Liquid Clustering in Fabric Runtime 2.0

Incremental Liquid Clustering in Fabric Runtime 2.0 is a new OPTIMIZE algorithm that processes only unclustered, small, or deletion-vector-heavy files instead of rewriting entire 100-GB groups, with Auto Reclustering to keep layout quality high—delivering up to 8.9x faster clustering and constant-time cost that scales with new data, not table size. Enabled by default with no configuration changes; use OPTIMIZE table FULL for a full recluster. For more information, see Apply liquid clustering on Delta tables.

Source: [fabric-whats-new-markdown](https://community.fabric.microsoft.com/t5/Fabric-Updates-Blog/Incremental-Liquid-Clustering-in-Microsoft-Fabric-Faster-smarter/ba-p/5189122)
